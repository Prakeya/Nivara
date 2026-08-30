# Nivara API

Base URL: `http://localhost:8000`

## Authentication

Optional. If the server is started with `NIVARA_API_KEY` set, every request must
include the header:

```
X-API-Key: <NIVARA_API_KEY>
```

Unset means open access (default for the hackathon demo).

## Error Encoding

| Status | Meaning |
|---|---|
| `400` | Invalid CSV payload |
| `404` | Unknown job / settlement |
| `422` | Batch rejected: exceeds Groq free-tier daily token budget |
| `429` | Per-IP rate limit exceeded |
| `503` | Server unhealthy (LLM/DB/disk check failed) |

---

## Startup

```bash
# Fail-fast: requires GROQ_API_KEY (deterministic engine runs without it,
# but AI investigation is disabled)
GROQ_API_KEY=... PYTHONPATH="." uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

---

## Endpoints

### `GET /health`

Deep health check: DB, LLM, disk.

```bash
curl -s http://localhost:8000/health
```

```json
{ "status": "ok", "version": "0.1.0" }
```

### `POST /upload`

Accepts the four CSVs as multipart form fields.

```bash
curl -s -X POST http://localhost:8000/upload \
  -F transactions=@data/demo/transactions.csv \
  -F settlements=@data/demo/settlements.csv \
  -F refunds=@data/demo/refunds.csv \
  -F bank_credits=@data/demo/bank_credits.csv
```

```json
{
  "job_id": "3f2a1c...",
  "upload_hash": "a1b2c3...",
  "status": "processing",
  "total_settlements": 80
}
```

Returns `422` if the batch would exceed the Groq free-tier daily token budget,
and `429` if the client is rate-limited.

### `GET /status/{job_id}`

Full dashboard payload: hero metrics, results table, review queue, batch
patterns, and audit records.

```bash
curl -s http://localhost:8000/status/3f2a1c...
```

### `GET /audit/{upload_hash}`

Append-only audit records for a batch.

```bash
curl -s http://localhost:8000/audit/a1b2c3...
```

### `GET /audit/{upload_hash}/verify`

Verify the SHA-256 hash chain; tampering yields `"valid": false`.

```bash
curl -s http://localhost:8000/audit/a1b2c3.../verify
```

```json
{ "valid": true, "total_records": 80, "broken_at": null }
```

### `GET /settlement/{settlement_id}`

Full audit history + decision for one settlement.

```bash
curl -s http://localhost:8000/settlement/SETL_0042
```

### `GET /api/review/pending`

Settlements queued for human review.

```bash
curl -s http://localhost:8000/api/review/pending
```

```json
{ "total_pending": 27, "settlements": [ { "settlement_id": "SETL_0042", "decision_state": "MATH_DISCREPANCY", "ai_classification": "UNEXPLAINED", "...": "..." } ] }
```

### `POST /api/review/{settlement_id}/decision`

Submit a human review decision (`APPROVE` | `REJECT`).

```bash
curl -s -X POST http://localhost:8000/api/review/SETL_0042/decision \
  -H "Content-Type: application/json" \
  -d '{"decision": "APPROVE", "reason": "Matches bank statement", "reviewer_id": "ops_team"}'
```

```json
{ "settlement_id": "SETL_0042", "decision": "APPROVE", "reason": "Matches bank statement", "reviewer_id": "ops_team", "timestamp": "2026-08-30T...", "status": "accepted" }
```

### `GET /api/metrics`

JSON metrics for the Metrics Dashboard (decision breakdown pie chart, Groq
free-tier quota progress, LLM latency/errors, cost).

```bash
curl -s http://localhost:8000/api/metrics
```

```json
{
  "generated_at": "2026-08-30T...",
  "active_ai": true,
  "batches_processed": 1,
  "settlements_processed": 80,
  "error_rate": 0.0,
  "avg_match_rate": 0.875,
  "decision_breakdown": { "clean": 26, "exceptions": 27, "math_discrepancy": 27, "unresolved": 0 },
  "ai_investigations_total": 27,
  "ai_auto_approved_total": 0,
  "llm": { "total_calls": 27, "errors": 0, "avg_latency_ms": 482.1, "error_rate": 0.0 },
  "groq_free_tier": { "daily_limit": 1000000, "used_tokens": 12150, "remaining_tokens": 987850, "pct_used": 1.22, "by_model": { "llama-3.1-70b-versatile": 12150 } },
  "estimated_cost_inr": 0.0
}
```

### `GET /metrics`

Prometheus exposition format (settlements, latency, LLM calls, upload errors,
audit decisions). Available when `prometheus_client` is installed.

```bash
curl -s http://localhost:8000/metrics
```

---

## Versioned API (`/v1`)

| Endpoint | Description |
|---|---|
| `GET /v1/health` | Alias of `/health` |
| `GET /v1/prompts` | List registered prompt versions |
| `GET /v1/jobs?page=1&page_size=50` | Paginated job list |
| `GET /v1/jobs/{job_id}/results?page=1&page_size=50` | Paginated results |
| `GET /v1/costs/{job_id}` | Cost summary for a job |
| `GET /v1/audit/{upload_hash}?page=1&page_size=50` | Paginated audit records |

```bash
curl -s "http://localhost:8000/v1/jobs?page=1&page_size=10"
```

```json
{ "items": [ { "job_id": "...", "status": "completed", "created_at": "..." } ], "total": 1, "page": 1, "page_size": 10, "total_pages": 1 }
```

---

## Generating the same CSVs used by the demo

```bash
PYTHONPATH="$(pwd)" python3 - <<'PY'
from backend.generator import generate_batch
import csv, pathlib
data = generate_batch()
out = pathlib.Path("data/demo")
out.mkdir(parents=True, exist_ok=True)
for name in ["transactions", "settlements", "refunds", "bank_credits"]:
    with (out / f"{name}.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=data[name][0].keys())
        w.writeheader(); w.writerows(data[name])
print("wrote 4 CSVs to data/demo/")
PY
```