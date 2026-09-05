# Nivara API

Base URL: `http://localhost:8000`

## Authentication and Roles

With no `NIVARA_API_KEY`, demo access is open. When it is set, send `X-API-Key`. Role mappings use `NIVARA_ROLE_<key>=admin|reviewer|viewer`.

| Method | Path | Auth permission | Description |
|---|---|---|---|
| POST | `/upload` | upload | Process four CSVs; return an audit-backed cached job for duplicate completed uploads |
| GET | `/status/{job_id}` | none | Read job results |
| GET | `/audit/{upload_hash}` | none | Read batch audit records |
| GET | `/audit/{upload_hash}/verify` | none | Verify SHA-256 audit chain |
| GET | `/settlement/{settlement_id}` | none | Read settlement history |
| POST | `/api/review/{settlement_id}/decision` | review | Submit human review decision |
| GET | `/api/review/pending` | read | List pending reviews |
| GET | `/api/review/{settlement_id}` | none | Read review status |
| POST | `/api/fetch-razorpay` | API key check | Fetch settlement rows only |
| POST | `/api/reconcile-razorpay` | API key check | Fetch Razorpay data and run reconciliation |
| GET | `/api/metrics` | configure | Read JSON metrics |
| GET | `/metrics` | none | Prometheus exposition when installed |
| GET | `/health` | none | Deep health check |

## UploadRequest and UploadResponse

`POST /upload` is multipart form data with required fields `transactions`, `settlements`, `refunds`, and `bank_credits`. Each field is a CSV file.

```bash
curl -X POST http://localhost:8000/upload \
  -F transactions=@data/demo/transactions.csv \
  -F settlements=@data/demo/settlements.csv \
  -F refunds=@data/demo/refunds.csv \
  -F bank_credits=@data/demo/bank_credits.csv
```

```json
{
  "job_id": "uuid-or-cached-job-id",
  "upload_hash": "sha256",
  "status": "completed",
  "message": "Batch already processed. Returning cached results."
}
```

A first completed upload returns HTTP 202 without the cache message. Invalid files return 4xx; rate limits return 429. The full result is available from `/status/{job_id}`.

## Status

```bash
curl http://localhost:8000/status/<job_id>
```

The completed response contains `total_settlements`, `clean_matches`, `exceptions`, `unresolved`, `math_discrepancies`, `match_rate`, `results`, `batch_analysis`, and `audit_records`.

## RazorpayFetchRequest

Both Razorpay POST endpoints accept JSON:

```json
{
  "from_date": "2026-08-01",
  "to_date": "2026-08-31",
  "count": 50,
  "days": 7
}
```

`from_date` and `to_date` are optional ISO dates. `count` defaults to 100. `days` is used by `/api/reconcile-razorpay` when `from_date` is omitted. Razorpay credentials are `RAZORPAY_API_KEY` and `RAZORPAY_API_SECRET`.

`POST /api/fetch-razorpay` returns fetched settlement CSV-shaped rows and does not run the engine:

```bash
curl -X POST http://localhost:8000/api/fetch-razorpay \
  -H 'Content-Type: application/json' \
  -d '{"days":7,"count":50}'
```

`POST /api/reconcile-razorpay` fetches settlements, payments, refunds, and transfers. When sandbox linkage is incomplete, it derives a payment and bank credit from each settlement before running the engine:

```bash
curl -X POST http://localhost:8000/api/reconcile-razorpay \
  -H 'Content-Type: application/json' \
  -d '{"from_date":"2026-08-01","to_date":"2026-08-31","count":50}'
```

```json
{
  "status": "completed",
  "job_id": "abc123",
  "total_settlements": 50,
  "clean_matches": 40,
  "exceptions": 10,
  "unresolved": 0,
  "match_rate": 80.0
}
```

## ReviewDecision

`POST /api/review/{settlement_id}/decision` requires a JSON body. `decision` is `APPROVE`, `REJECT`, or `MODIFY`; `reason` and `reviewer_id` are strings.

```bash
curl -X POST http://localhost:8000/api/review/SETL_0042/decision \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: reviewer-key' \
  -d '{"decision":"APPROVE","reason":"Verified against bank statement","reviewer_id":"ops"}'
```

```json
{
  "settlement_id": "SETL_0042",
  "decision": "APPROVE",
  "reason": "Verified against bank statement",
  "reviewer_id": "ops",
  "timestamp": "2026-09-05T12:00:00+00:00",
  "status": "accepted"
}
```

`GET /api/review/pending` returns `{ "total_pending": 0, "settlements": [] }`. `GET /api/review/{settlement_id}` returns whether the case has been reviewed and its current result when available.

## AuditRecord

`GET /audit/{upload_hash}` returns records with:

```json
{
  "id": "uuid",
  "upload_hash": "sha256",
  "settlement_id": "SETL_0042",
  "timestamp": "2026-09-05T12:00:00+00:00",
  "decision_state": "CLEAN_MATCH",
  "payload_json": "{...redacted...}",
  "record_hash": "sha256",
  "prev_hash": "sha256-or-empty"
}
```

```bash
curl http://localhost:8000/audit/<upload_hash>
curl http://localhost:8000/audit/<upload_hash>/verify
curl http://localhost:8000/settlement/SETL_0042
```

## MetricsResponse

`GET /api/metrics` returns generated time, job/settlement counts, decision breakdown, AI counts, LLM snapshot, Groq quota snapshot, and estimated cost fields. `GET /metrics` returns Prometheus text when `prometheus-client` is installed; otherwise the application returns its fallback text response.

```bash
curl -H 'X-API-Key: admin-key' http://localhost:8000/api/metrics
curl http://localhost:8000/metrics
```

## Health

```bash
curl http://localhost:8000/health
```

The deep health response reports overall status and database, LLM, and disk checks.

## Environment

See `.env.example`. `GROQ_API_KEY` is required at application startup. Razorpay keys enable live import. `NIVARA_API_KEY` enables RBAC; `NIVARA_DATABASE_URL` selects the optional database abstraction branch.
