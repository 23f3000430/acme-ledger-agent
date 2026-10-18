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
