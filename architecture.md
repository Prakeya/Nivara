# Nivara Architecture

Nivara reconciles Standard Checkout settlement data through a synchronous FastAPI application. The deterministic engine owns financial arithmetic; the Groq path is advisory and only investigates math discrepancies.

## System Flow

```mermaid
flowchart TD
    UI[React UploadPanel\nCSV upload or Razorpay date selector]
    CSV[POST /upload\n4 CSV files]
    LIVE[POST /api/reconcile-razorpay]
    MCP[RazorpayMCPClient\nsettlements, payments, refunds, transfers]
    FALLBACK[Settlement-derived payment and bank-credit fallback\nwhen sandbox linkage is incomplete]
    HASH[compute_upload_hash\nCSV idempotency key]
    CACHE{Completed cached batch?}
    INGEST[ingest_csvs\nschema validation and normalization]
    LINK[Entity linking\npayments, refunds, bank credits]
    ENGINE[Deterministic engine\n12 checks, integer paise arithmetic]
    EVIDENCE[EvidencePacketV2]
    GUARD[Deterministic guard\nAI only for MATH_DISCREPANCY]
    FINAL[Final deterministic result]
    GROQ[GroqClient + model selector\n70B then 8B fallback]
    VALIDATE[AI response and citation validation]
    UNRESOLVED[UNRESOLVED]
    REVIEW[Human review queue\n/api/review/pending]
    AUDIT[AuditLogger\nSHA-256 chain + PII redaction]
    OBS[Metrics and logging\n/metrics, /api/metrics, CorrelationMiddleware]
    RBAC[RBAC dependencies\nrequire_upload/review/read/configure]

    UI --> CSV
    UI --> LIVE
    CSV --> RBAC --> HASH --> CACHE
    CACHE -- yes --> FINAL
    CACHE -- no --> INGEST
    LIVE --> RBAC --> MCP
    MCP --> FALLBACK
    MCP --> INGEST
    FALLBACK --> INGEST
    INGEST --> LINK --> ENGINE --> EVIDENCE --> GUARD
    GUARD -- CLEAN_MATCH or DETERMINISTIC_EXCEPTION --> FINAL
    GUARD -- MATH_DISCREPANCY --> GROQ --> VALIDATE
    VALIDATE -- valid advisory response --> REVIEW
    VALIDATE -- invalid or failed --> UNRESOLVED --> REVIEW
    REVIEW --> FINAL
    FINAL --> AUDIT
    ENGINE --> OBS
    AUDIT --> OBS
```

## Component Responsibilities

- **React frontend:** selects four CSV files, sends uploads, and offers the Razorpay date-range selector.
- **RBAC layer:** checks `X-API-Key` roles. Upload requires upload permission; review decisions require review permission; pending review reads require read permission; JSON metrics require configure permission. When `NIVARA_API_KEY` is unset, the dependency grants demo admin access.
- **Upload hash and cache:** the upload handler computes the canonical hash, checks for a completed in-memory job with durable audit records, and returns the cached job without reprocessing.
- **Razorpay MCP client:** fetches settlements and, for reconciliation, payments, refunds, and transfers. If sandbox collections do not provide complete linkage, the endpoint derives a matching payment and bank credit from each settlement.
- **Ingestion:** validates and normalizes the four CSV inputs into engine dictionaries.
- **Entity linker:** resolves authoritative settlement payment/refund IDs and matches bank credits by UTR and amount/date rules.
- **Deterministic engine:** performs twelve checks and computes expected amounts and differences using integer paise arithmetic.
- **EvidencePacketV2:** carries structured summaries and deterministic evidence to the AI investigator.
- **Deterministic guard:** `should_invoke_ai()` permits AI only for `MATH_DISCREPANCY`; deterministic outcomes remain authoritative.
- **Groq client and fallback chain:** selects the 70B or 8B Groq model, applies rate and circuit-breaker controls, and fails to unresolved when calls fail.
- **AI validator:** parses response fields and validates citations against the evidence packet.
- **Human review queue:** exposes pending cases and accepts APPROVE, REJECT, or MODIFY decisions.
- **Audit logger:** persists append-only records using SHA-256 payload/previous-hash chaining. `redact_dict()` removes PII from explanations, notes, reviewer IDs, and nested payload fields before persistence.
- **Observability:** Prometheus metrics are available when the optional client is installed; JSON dashboard metrics are always backed by in-memory trackers. `CorrelationMiddleware` propagates and returns `X-Request-ID`.

## Decision State Machine

```mermaid
stateDiagram-v2
    [*] --> CLEAN_MATCH
    [*] --> DETERMINISTIC_EXCEPTION
    [*] --> MATH_DISCREPANCY
    [*] --> UNRESOLVED
    CLEAN_MATCH --> Final
    DETERMINISTIC_EXCEPTION --> Final
    MATH_DISCREPANCY --> AI_Investigation
    AI_Investigation --> REVIEW_REQUIRED: valid advisory response
    AI_Investigation --> UNRESOLVED: invalid/failed response
    REVIEW_REQUIRED --> Human_Review
    UNRESOLVED --> Human_Review
    Human_Review --> Final
```

`CLEAN_MATCH` and `DETERMINISTIC_EXCEPTION` are final deterministic outcomes. `MATH_DISCREPANCY` receives optional AI investigation and remains human-reviewable. Invalid or unavailable AI becomes `UNRESOLVED`.

## Data and Persistence

The active audit path is SQLite at `data/audit/audit.db`, and jobs are held in the process-local `_jobs` store. `backend/database.py` contains a separate optional PostgreSQL abstraction selected by `NIVARA_DATABASE_URL`; it is not currently used by `AuditLogger`. Redis/Celery support is present in optional task code but is not used by the synchronous upload handler.

## Scope

Supported input is Razorpay Standard Checkout settlement reconciliation in CSV or the live settlement integration. RazorpayX payouts, Smart Collect, Route splitting, multi-currency processing, and real-time webhook processing are not implemented.
