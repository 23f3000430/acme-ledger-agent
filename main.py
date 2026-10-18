import json
import re
import os
from collections import defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

APP_DIR = Path(__file__).resolve().parent
TZ = ZoneInfo("Asia/Kolkata")
USD_PER_UNIT = {"USD": 1.0, "EUR": 1.09, "INR": 0.01212}
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
    "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
    "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12
}
REGIONS = ["North", "South", "East", "West", "Central"]
PRODUCTS = ["Air Fryer", "Rice Cooker", "Kettle", "Toaster", "Juicer", "Grinder", "Mixer", "Blender"]

def parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except Exception:
        return None

def load_orders():
    path = APP_DIR / "orders.jsonl"
    latest = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            oid = str(row.get("id", ""))
            if not oid:
                continue
            old = latest.get(oid)
            current_dt = parse_dt(row.get("updated_at")) or datetime.min.replace(tzinfo=TZ)
            old_dt = parse_dt(old.get("updated_at")) if old else None
            if old is None or old_dt is None or current_dt >= old_dt:
                latest[oid] = row
    return list(latest.values())

ORDERS = load_orders()
app = FastAPI(title="Acme Appliances Ledger Agent", version="1.0.0")

class QuestionRequest(BaseModel):
    question: str

def amount_usd(row):
    currency = str(row.get("currency", "USD")).upper()
    rate = USD_PER_UNIT.get(currency)
    if rate is None:
        return 0.0
    return float(row.get("amount", 0) or 0) * rate

def extract_date_bounds(q):
    """Return inclusive start date and exclusive end date in Asia/Kolkata."""
    ql = q.lower()
    start = end = None
    # Explicit YYYY-MM-DD range, if present.
    dates = re.findall(r"\b(20\d{2}-\d{1,2}-\d{1,2})\b", ql)
    if len(dates) >= 2:
        try:
            d1 = date.fromisoformat(dates[0])
            d2 = date.fromisoformat(dates[1])
            return min(d1, d2), max(d1, d2) + timedelta(days=1)
        except ValueError:
            pass
    if len(dates) == 1:
        try:
            d = date.fromisoformat(dates[0])
            return d, d + timedelta(days=1)
        except ValueError:
            pass

    year_match = re.search(r"\b(20\d{2})\b", ql)
    year = int(year_match.group(1)) if year_match else None
    # Quarter, e.g. Q1 2026 or first quarter of 2026.
    qm = re.search(r"\bq([1-4])\b", ql)
    if qm:
        y = year or 2026
        m = (int(qm.group(1)) - 1) * 3 + 1
        return date(y, m, 1), date(y + (m + 2) // 12, ((m + 2) % 12) + 1, 1)
    quarter_words = {
        "first quarter": 1, "1st quarter": 1, "second quarter": 2, "2nd quarter": 2,
        "third quarter": 3, "3rd quarter": 3, "fourth quarter": 4, "4th quarter": 4
    }
    for phrase, quarter in quarter_words.items():
        if phrase in ql:
            y = year or 2026
            m = (quarter - 1) * 3 + 1
            end_m = m + 3
            return date(y, m, 1), date(y + (end_m - 1) // 12, ((end_m - 1) % 12) + 1, 1)

    found_month = None
    for name, num in MONTHS.items():
        if re.search(r"\b" + re.escape(name) + r"\b", ql):
            found_month = num
            break
    if found_month:
        y = year or 2026
        if found_month == 12:
            return date(y, 12, 1), date(y + 1, 1, 1)
        return date(y, found_month, 1), date(y, found_month + 1, 1)
    if year:
        return date(year, 1, 1), date(year + 1, 1, 1)
    return None, None

def matches_filters(row, q, start, end):
    ql = q.lower()
    dt = parse_dt(row.get("created_at"))
    if start:
        if dt is None or dt.date() < start:
            return False
    if end:
        if dt is None or dt.date() >= end:
            return False
    for region in REGIONS:
        if re.search(r"\b" + region.lower() + r"\b", ql) and row.get("region", "").lower() != region.lower():
            return False
    for product in sorted(PRODUCTS, key=len, reverse=True):
        if product.lower() in ql and row.get("product", "").lower() != product.lower():
            return False
    customer = re.search(r"\bC\d{3,5}\b", q, re.I)
    if customer and str(row.get("customer", "")).upper() != customer.group(0).upper():
        return False
    # Explicit status filters; revenue/refund intent sets a default status separately.
    for status in ("paid", "refunded", "void", "pending", "cancelled", "canceled"):
        if re.search(r"\b" + status + r"\b", ql) and str(row.get("status", "")).lower() != status:
            return False
    return True

def infer_status(q):
    ql = q.lower()
    if re.search(r"\b(refund|refunded|refunds|refunded amount)\b", ql):
        return "refunded"
    # Include common forms of "earn" (e.g. "How much did North earn?").
    if re.search(r"\b(revenue|sales|turnover|sold|spend|spent|earn|earned|earns|earning|earnings|income)\b", ql):
        return "paid"
    return None

def infer_metric(q):
    ql = q.lower()
    if "refund rate" in ql or "percentage of orders refunded" in ql:
        return "refund_rate"
    if any(x in ql for x in ["average order value", "average value per order", "avg order value"]):
        return "average"
    if any(x in ql for x in ["unique customer", "distinct customer", "how many customers", "number of customers", "customer count"]):
        return "customers"
    if any(x in ql for x in ["unit", "quantity", "quantities", "items sold", "pieces sold"]):
        return "units"
    if any(x in ql for x in ["how many orders", "number of orders", "count of orders", "order count", "count orders", "orders were"]):
        return "orders"
    # Handle natural count phrasing such as "Count the refunded South orders...".
    if re.search(r"\bcount\b", ql) and re.search(r"\borders?\b", ql):
        return "orders"
    if any(x in ql for x in ["which", "top", "highest", "most", "least", "best", "largest", "lowest", "fewest"]):
        return "rank"
    return "sum"

def infer_group(q):
    ql = q.lower()
    if "product" in ql or any(p.lower() in ql for p in PRODUCTS):
        return "product"
    if "region" in ql or any(r.lower() in ql for r in REGIONS):
        return "region"
    if "customer" in ql or re.search(r"\bC\d{3,5}\b", q, re.I):
        return "customer"
    return None

def value_for(row, metric):
    if metric in ("sum", "rank", "average"):
        return amount_usd(row)
    if metric == "units":
        return float(row.get("qty", 0) or 0)
    if metric == "orders":
        return 1.0
    return 0.0

def money_round(x):
    return round(float(x) + 1e-9, 2)

def answer_question(question):
    if not question or not question.strip():
        raise ValueError("question must not be empty")
    q = question.strip()
    ql = q.lower()
    start, end = extract_date_bounds(q)
    filtered = [r for r in ORDERS if matches_filters(r, q, start, end)]

    # Infer whether the question is specifically about refunds/revenue.
    status = infer_status(q)
    if status:
        filtered = [r for r in filtered if str(r.get("status", "")).lower() == status]

    metric = infer_metric(q)
    group = infer_group(q)

    if metric == "refund_rate":
        total = [r for r in ORDERS if matches_filters(r, q, start, end)]
        if not total:
            return 0
        refunded = sum(1 for r in total if str(r.get("status", "")).lower() == "refunded")
        return round(100 * refunded / len(total), 2)

    if metric == "customers":
        return len({r.get("customer") for r in filtered if r.get("customer")})

    if metric == "rank" and group:
        buckets = defaultdict(float)
        for row in filtered:
            key = row.get(group)
            if key:
                if metric == "rank":
                    # Rank by revenue unless the wording explicitly asks for volume/count.
                    if any(x in ql for x in ["units", "quantity", "items sold", "pieces sold"]):
                        buckets[key] += float(row.get("qty", 0) or 0)
                    elif any(x in ql for x in ["number of orders", "order count", "most orders", "orders"]):
                        buckets[key] += 1
                    else:
                        buckets[key] += amount_usd(row)
        if not buckets:
            return "No matching records"
        reverse = not any(x in ql for x in ["least", "lowest", "fewest"])
        best = sorted(buckets.items(), key=lambda x: (x[1], str(x[0])), reverse=reverse)
        top_n_match = re.search(r"\btop\s+(\d+)\b", ql)
        if top_n_match:
            n = min(int(top_n_match.group(1)), 10)
            return [{"name": k, "value": money_round(v)} for k, v in best[:n]]
        return best[0][0]

    if metric == "average":
        if not filtered:
            return 0
        return money_round(sum(amount_usd(r) for r in filtered) / len(filtered))

    if metric == "units":
        return int(sum(float(r.get("qty", 0) or 0) for r in filtered))

    if metric == "orders":
        return len(filtered)

    total = sum(amount_usd(r) for r in filtered)
    return money_round(total)

@app.get("/")
def root_get():
    return {"service": "Acme Appliances ledger agent", "status": "ok", "orders_loaded": len(ORDERS)}

@app.get("/health")
def health():
    return {"status": "ok", "orders_loaded": len(ORDERS)}

@app.post("/")
@app.post("/answer")
def answer(req: QuestionRequest):
    try:
        result = answer_question(req.question)
        return {"answer": result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Unable to process question")
