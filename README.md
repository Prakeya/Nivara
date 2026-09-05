# Nivara

Nivara is a settlement-reconciliation application for Razorpay Standard Checkout data. It combines deterministic integer-paise checks, optional Groq investigation, human review, and an append-only SHA-256 audit trail.

> "If the project works and the code is clean, it's already ahead of 95% of the other submissions."
> — Razorpay Buildathon judge

Updated with idempotency, live fetch, RBAC, and observability per post-review iteration.

## Results

- **Tests:** 705 passing
- **Backend coverage:** 86.46% (`pytest --cov=backend --cov-report=term -q`)
- **Compile check:** `COMPILE_OK`
- **Frontend build:** Vite build passes
- **AI auto-approval:** AI remains advisory and review decisions are explicit

## Architecture

```mermaid
flowchart TD
    UI[React UI] --> CSV[POST /upload]
    UI --> LIVE[POST /api/reconcile-razorpay]
    CSV --> HASH[compute upload hash]
    HASH --> CACHE{Completed audit-backed cache?}
    CACHE -- yes --> RESULT[Return cached job]
    CACHE -- no --> INGEST[Ingest and validate CSVs]
    LIVE --> MCP[Fetch settlements/payments/refunds/transfers]
    MCP --> BRIDGE[Derive fallback rows if sandbox linkage is incomplete]
    BRIDGE --> INGEST
    INGEST --> ENGINE[Deterministic engine: 12 checks]
    ENGINE --> EVIDENCE[EvidencePacketV2]
    EVIDENCE --> GUARD[Deterministic guard]
    GUARD -- clean/exception --> RESULT
    GUARD -- math discrepancy --> GROQ[Groq 70B/8B fallback]
    GROQ --> VALIDATE[AI response and citation validation]
    VALIDATE --> REVIEW[Human review queue]
    REVIEW --> RESULT
    RESULT --> AUDIT[SHA-256 audit chain with PII redaction]
    ENGINE --> METRICS[/metrics and /api/metrics]
    UI --> DATE[Last 24h/7d/30d selector]
```

CSV uploads are hashed before ingestion. Completed jobs with durable audit records are returned from the cache. The live endpoint fetches matching Razorpay collections when available and derives transparent demo rows when a sandbox account does not expose complete linkage. Both paths then use the same deterministic engine and audit flow.

## Working Features

- Four-file CSV ingestion and validation.
- Twelve deterministic checks with integer paise arithmetic.
- EvidencePacketV2 structured evidence.
- Deterministic guard and citation validation for AI responses.
- Groq 70B primary and 8B fallback model path with rate limiting/circuit breaker support.
- Upload idempotency using the canonical `upload_hash`.
- Razorpay live import and reconciliation at `/api/reconcile-razorpay`.
- RBAC for upload, review, read, and configure permissions.
- PII redaction before audit persistence.
- SHA-256 hash-chained audit records.
- Human review queue and decision endpoint.
- JSON metrics, optional Prometheus exposition, and correlation request IDs.
- React/Vite dashboard with Razorpay date-range selection.

DeterministicGuard, AIValidator, and ModelSelector are fully wired into the `/upload` and `/api/reconcile-razorpay` pipelines. The Guard prevents AI override, the Selector chooses a model by evidence complexity, and the Validator rejects invalid citations before results reach human reviewers.

## Current Scope & Limitations

- Checkout settlements only.
- RazorpayX payouts, Smart Collect virtual accounts, and Route split settlements are not implemented.
- Live fetch uses derived transaction and bank-credit fallback rows when full sandbox linkage is unavailable.
- The active audit logger is SQLite and the process-local job store is in memory. `backend/database.py` contains an optional PostgreSQL branch selected by `NIVARA_DATABASE_URL`, but it is not currently the active `AuditLogger` backend.
- Redis/Celery support is optional code and is not used by the synchronous API path.
- Screenshots in `docs/screenshots/` were removed because they showed the previous UI; updated screenshots are pending.

## Quick Start

```bash
git clone <repository-url>
cd Nivara
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
export GROQ_API_KEY=your_groq_key
uvicorn backend.main:app --reload
```

For the frontend development server:

```bash
cd frontend
npm install
npm run dev
```

Set `RAZORPAY_API_KEY` and `RAZORPAY_API_SECRET` to enable live import. The API fails fast at startup when `GROQ_API_KEY` is missing.

## API Summary

See [API.md](API.md) for request/response schemas and curl examples.

| Method | Path | Purpose |
|---|---|---|
| POST | `/upload` | Process four CSV files; return cached completed job when available |
| GET | `/status/{job_id}` | Read job status and results |
| GET | `/audit/{upload_hash}` | Read batch audit records |
| GET | `/audit/{upload_hash}/verify` | Verify the hash chain |
| GET | `/settlement/{settlement_id}` | Read settlement audit history |
| POST | `/api/review/{settlement_id}/decision` | Submit human decision |
| GET | `/api/review/pending` | List pending review cases |
| GET | `/api/review/{settlement_id}` | Read review status |
| POST | `/api/fetch-razorpay` | Import settlement rows without reconciliation |
| POST | `/api/reconcile-razorpay` | Fetch and reconcile Razorpay data |
| GET | `/api/metrics` | JSON dashboard metrics |
| GET | `/metrics` | Prometheus exposition |
| GET | `/health` | Deep health check |

When `NIVARA_API_KEY` is set, use `X-API-Key`. Roles are configured with `NIVARA_ROLE_<key>=admin|reviewer|viewer`.

**Security warning:** if `NIVARA_API_KEY` is left unset, RBAC is bypassed entirely and every request is treated as `Role.ADMIN` — no key required (see `backend/rbac.py`). This is a demo-friendly default, not a safe one. Do not deploy this service anywhere reachable by untrusted callers without setting `NIVARA_API_KEY`.

## Tech Stack

| Layer | Packages/technology |
|---|---|
| Backend | Python 3.11+, FastAPI, Uvicorn, Pydantic v2, Pandas, httpx |
| AI | Groq SDK; optional OpenAI import in investigator code |
| Persistence | SQLite audit logger; optional psycopg2 database abstraction |
| Metrics | Optional prometheus-client plus in-memory JSON metrics |
| Frontend | React 18, Vite, Recharts |
| Testing | pytest, pytest-cov |

## Testing

```bash
pytest -q
pytest --cov=backend --cov-report=term -q
python3 -m compileall -q backend
cd frontend && npm run build
```

The measured baseline is 705 passing tests and 86.46% backend coverage. A dedicated mypy/bandit clean result is not claimed because those tools are not configured as repository quality gates.

## Scaling to Production

The current demo uses SQLite and an in-memory job store. `NIVARA_DATABASE_URL` selects the optional PostgreSQL connection branch in `backend/database.py`; activating the full production persistence path requires wiring that abstraction into the audit/job services. Compose provisions PostgreSQL 15 and Redis 7 for the production-oriented deployment shape.

```bash
docker build -t nivara .
docker compose up --build
```

## License

MIT. Built for the Razorpay Buildathon 2026, Track 04: AI Finance Controller.
