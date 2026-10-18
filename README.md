# Acme Appliances Ledger Agent

A small FastAPI service for answering questions about the provided Acme order ledger.

## What it does

- Reads the supplied JSONL export (`orders.jsonl`) at startup.
- Deduplicates order IDs, keeping the row with the latest `updated_at`.
- Converts EUR and INR amounts to USD using the assignment's supplied rates:
  - USD = 1.0
  - EUR = 1.09
  - INR = 0.01212
- Interprets business dates in `Asia/Kolkata`.
- Accepts `POST /` or `POST /answer` with `{"question":"..."}` and returns `{"answer": ...}`.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Try:

```bash
curl -X POST http://127.0.0.1:8000/ \
  -H 'Content-Type: application/json' \
  -d '{"question":"What was the total revenue in USD from the North region in March 2026?"}'
```

`GET /health` reports whether the service is running. `GET /` reports the number of unique order IDs loaded.

## Deploy to Render

1. Create a GitHub repository and upload `main.py`, `requirements.txt`, `render.yaml`, and `orders.jsonl`.
2. In Render, choose **New + → Blueprint** and connect the repository (or create a Web Service with the same build/start commands).
3. Wait for deployment. Copy the public HTTPS URL Render provides.
4. Submit that public URL in the assignment form. The service accepts POST requests at the root URL.

## Important limitations

This is a deterministic question parser, not a general-purpose LLM. It supports common questions about revenue, refunds, product/customer/region rankings, order counts, units, dates, and customer IDs. Before submitting, test it against the assignment's example questions and any available test interface. If your grader asks a question outside these patterns, the parser may need an additional rule.
