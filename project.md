# Nivara

Nivara is a FastAPI and React settlement-reconciliation application for Razorpay Standard Checkout data. It accepts four CSV files or imports Razorpay settlements through the live integration, then produces deterministic results, optional Groq investigation, human-review state, and append-only audit records.

## Problem

Merchants must reconcile payment transactions, refunds, settlement reports, and bank credits. Nivara checks references, linkage, fees, taxes, bank credits, UTRs, and settlement arithmetic using integer paise values.

## Solution

The deterministic engine owns financial calculations. MATH_DISCREPANCY cases can be investigated with structured evidence through the Groq fallback chain; AI output is validated and remains advisory. Human review decisions are submitted through the review API and written to the audit log.

## Implemented Features

- Twelve deterministic reconciliation checks in `backend/engine.py`.
- `EvidencePacketV2` structured evidence contract.
- Deterministic guard functions that limit AI investigation to `MATH_DISCREPANCY`.
- AI response citation validation in `backend/ai_validator.py` and `backend/ai_investigator.py`.
- Groq client with 70B primary and 8B fallback model selection.
- Groq fallback chain with rate limiting and circuit breaker support.
- Upload hash idempotency: completed uploads with audit records return the cached job.
- Razorpay live settlement, payment, refund, and transfer fetch through `/api/reconcile-razorpay`.
- Settlement-derived transaction and bank-credit fallback when sandbox linkage is incomplete.
- RBAC dependencies for upload, review, read, and configure permissions.
- PII redaction in persisted audit payloads.
- Prometheus-compatible metrics when `prometheus-client` is installed, JSON metrics, and correlation-ID middleware.
- SHA-256 hash-chained append-only audit records.
- Human review queue and decision endpoint.

## Architecture Principles

- **Deterministic veto:** deterministic exceptions remain exceptions.
- **Advisory-only AI:** AI does not calculate amounts or approve financial decisions.
- **Evidence-bound explanations:** cited evidence must exist in the evidence packet.
- **Fail-safe uncertainty:** failed or invalid AI investigation becomes unresolved/human-review work.
- **Traceability:** upload hashes, audit records, decision states, and request IDs are observable.

## Current Limitations

- Standard Checkout settlements only.
- RazorpayX payouts, Smart Collect virtual accounts, and Route split settlements are not implemented.
- Live fetch uses settlement-derived transaction and bank-credit rows when sandbox collections do not provide complete linkage.
- The main upload/reconciliation audit path uses SQLite at `data/audit/audit.db`; the database abstraction contains an optional PostgreSQL branch (used by the deep health check), but it is not the active audit logger path.
- No async task queue — all processing is synchronous within the request handler.

## Tech Stack

| Area | Implemented technology |
|---|---|
| Runtime | Python 3.12 Docker image; Python 3.11+ compatible code |
| API | FastAPI, Uvicorn, Starlette |
| Models | Pydantic v2 |
| Ingestion | Python CSV parsing and Pandas |
| AI | Groq SDK, optional OpenAI import used by the investigator |
| HTTP | httpx |
| Persistence | SQLite audit logger; optional psycopg2 database abstraction |
| Metrics | prometheus-client when installed, plus in-memory JSON metrics |
| Frontend | React 18, Vite, Recharts |
| Tests | pytest and pytest-cov |

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
pytest -q
pytest --cov=backend --cov-report=term -q
python3 -m compileall -q backend
```

Start the API:

```bash
GROQ_API_KEY=your_key uvicorn backend.main:app --reload
```

Start the frontend separately:

```bash
cd frontend
npm install
npm run dev
```

Generate sample files with `python3 scripts/demo.py` or use the checked-in files under `data/demo/` and `data/evaluation/`.

## Deployment

```bash
docker build -t nivara .
docker compose up --build
```

The compose file provisions PostgreSQL and Redis services for the production-oriented stack, while the current main audit path remains SQLite unless the application integration is changed.
