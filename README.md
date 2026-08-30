# NIVARA — AI Settlement Controller

**Track:** Razorpay Buildathon 2026 — Track 04: AI Finance Controller

> **87.5% match rate on 80-settlement evaluation dataset**
> 505 tests, tamper-proof audit trail, 726+ settlements/sec
> AI investigates. Humans decide. Zero auto-approvals.

---

## The Problem

Razorpay processes millions of settlements daily. When amounts don't match — fee miscalculations, missing bank credits, refund timing issues, duplicate records — someone has to figure out why. Manual investigation takes 15+ minutes per settlement, and naive "send everything to an LLM" approaches are too risky for financial decisions.

## The Solution

Nivara separates what can be proven from what requires reasoning:

- **Deterministic Engine** — 12 validation checks (fee, tax, bank credit, duplicate detection, linkage consistency, UTR cross-check, amount cross-check, expected amount calculation, adjustment consistency, etc.). All financial arithmetic uses integer paise — no floating-point.
- **AI Investigation** — When the engine flags an exception, an AI agent gathers structured evidence, checks cross-settlement patterns, and generates a classification report with citations. The AI cannot calculate financial amounts or modify records.
- **Human Review Queue** — All exceptions are presented to humans with evidence packets, deterministic check results, AI classification, and confidence scores. Humans click Approve or Reject.
- **Tamper-Proof Audit** — Every decision is recorded in an append-only SQLite database with SHA-256 hash chaining. Click "Verify Integrity" in the Audit Trail tab — 160+ records, zero tampering.
- **Batch Pattern Detection** — Cross-settlement analysis catches systemic issues like repeated bank delays, fee rounding clusters, and refund anomalies.

---

## How Nivara Works

```
Upload CSVs → Schema Validation → Entity Linking → Deterministic Rules
                                                        │
                                            ┌───────────┴───────────┐
                                            │                       │
                                       CLEAN_MATCH              DISCREPANCY
                                            │                       │
                                            │               LLM Exception Analyzer
                                            │               (structured evidence in,
                                            │                classification + citations out)
                                            │                       │
                                            └───────────┬───────────┘
                                                        │
                                                 Human Review Queue
                                                        │
                                                  Audit Trail (SHA-256)
```

---

## Screenshots

> **Dashboard** — 5-card metrics with match rate, exception breakdown, and blind spots
> **Trace View** — Full reconciliation trace with deterministic checks and AI investigation
> **Review Queue** — Human review with Approve/Reject buttons and evidence packets
> **Audit Trail** — Verify Integrity button proves tamper-proof hash chain
> **Batch Patterns** — Cross-settlement pattern detection with severity levels

---

## Comparison

| Capability | Manual Reconciliation | Simple Rule Engine | Nivara |
|---|---|---|---|
| Matching | Human eyeballs | Fixed rules | 12 deterministic checks + AI investigation |
| Evidence | Raw CSVs | None | Structured evidence packets with citations |
| Audit | Spreadsheets | Logs | Append-only SQLite with SHA-256 hash chain |
| Patterns | Per-settlement | None | Cross-settlement batch analysis |
| Review | Fully manual | Auto-approve/reject | AI-assisted human-in-the-loop |
| Speed | 15 min/settlement | Instant | 0.001s engine + human review |
| Safety | Trust the human | Trust the rules | Schema-enforced: AI never auto-approves |

---

## Why Nivara Is Not Just an LLM Wrapper

| Conventional AI Reconciliation | Nivara |
|---|---|
| Sends raw CSV to an LLM | Sends structured evidence packets |
| LLM calculates financial values | Python performs all financial calculations |
| AI decides whether money matches | Deterministic engine determines correctness |
| AI can generate unsupported explanations | Evidence citations are validated against the evidence packet |
| AI may approve automatically | AI always escalates to human — schema-enforced |
| Single demonstration example | 80-settlement ground-truth evaluation dataset |
| Failures may produce guesses | Failures become UNRESOLVED + human review |

---

## Evaluation

### Results on 80-Settlement Ground-Truth Dataset

| Metric | Value |
|---|---|
| **Match Rate** | 87.5% (70/80 correctly handled) |
| **False Accept Rate** | 12.5% (10 missed — known blind spots) |
| **Throughput** | 726+ settlements/sec |
| **AI Auto-Approval Rate** | 0% (enforced by schema) |
| **Test Count** | 505 passing |
| **Audit Records** | Append-only with SHA-256 hash chain |

### Edge-Case Categories (12 total)

| Ground Truth | Count | Engine Behavior |
|---|---|---|
| Clean Match | 26 | Correctly identified as clean |
| Duplicate Detection | 4 | Caught — DET-EXCEPTION |
| Missing Reference | 5 | Caught — DET-EXCEPTION |
| Bank Mismatch | 5 | Caught — DET-EXCEPTION |
| Fee Mismatch | 5 | Caught — DET-EXCEPTION |
| Tax Inconsistency | 3 | Caught — DET-EXCEPTION |
| Refund Timing | 5 | Caught — MATH_DISCREPANCY |
| Adjustment Entry | 5 | Caught — MATH_DISCREPANCY |
| Partial Settlement | 4 | Caught — DET-EXCEPTION |
| Unexplained | 8 | Caught — MATH_DISCREPANCY |
| **Refund After Settlement** | 5 | **Missed** — engine blind spot |
| **Timing Race** | 5 | **Missed** — engine blind spot |

### Known Limitations

**The deterministic engine has 2 known blind spots:**

- **refund_after_settlement** — A refund processed after settlement is not in `linked_refund_ids`. The engine computes expected = payments - refunds - fees - tax, finds difference == 0, and returns CLEAN_MATCH. But the merchant was overpaid.
- **timing_race** — A refund created during the settlement processing window is not linked. Same outcome — engine says clean, but the refund should have been deducted.

These false negatives are **honest** — they show where the deterministic engine needs live LLM investigation or additional business rules. With a live LLM, classification coverage increases significantly.

**The dataset is synthetic and co-designed with the engine.** A 100% match rate would mean the engine catches every case it was built to catch — which is expected, not impressive. The two blind-spot categories deliberately expose where deterministic rules alone are insufficient.

---

## Safety Architecture

### 1. AI Never Calculates Money
All financial arithmetic is performed by deterministic Python code using integer paise values.

### 2. AI Never Modifies Financial Records
The LLM response schema does not contain financial fields (amount, fee, refund, tax, expected_amount, actual_amount). Unexpected fields are rejected.

### 3. AI Never Auto-Approves
LLM recommendations are restricted to `ESCALATE_TO_HUMAN`. The system enforces `auto_approved_by_ai == 0`.

### 4. AI Claims Require Evidence
AI explanations must reference evidence identifiers supplied by the evidence packet. Unsupported evidence is rejected.

### 5. AI Failures Fail Safely
Timeouts, malformed responses, API failures, and invalid evidence result in `UNRESOLVED` + human review. The system does not silently approve uncertain financial cases.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run tests (505 should pass)
PYTHONPATH="$(pwd)" python3 -m pytest -q

# 3. Generate demo data & run evaluation
PYTHONPATH="$(pwd)" python3 -m backend.evaluation

# 4. Start the API + frontend
PYTHONPATH="$(pwd)" uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# 5. Open dashboard
open http://localhost:8000
```

### One-Command Demo

```bash
python3 demo.py
```

This will:
1. Generate 80 synthetic settlements with ground truth
2. Run the full test suite (505 tests)
3. Ingest → Link → Reconcile → AI Investigate → Evaluate
4. Print match rate, per-class F1, and throughput

---

## API

```
GET  /health                        — Health check
POST /upload                        — Upload 4 CSVs, return job_id
GET  /status/{job_id}               — Processing status + results
GET  /audit/{upload_hash}           — Audit trail for a batch
GET  /audit/{upload_hash}/verify    — Verify hash chain integrity
GET  /settlement/{settlement_id}    — Settlement audit history
POST /api/review/{id}/decision      — Submit human review
GET  /api/review/pending            — List pending reviews
```

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| API | FastAPI |
| Validation | Pydantic v2 |
| Data Processing | Python / CSV |
| Database | SQLite (append-only audit) |
| AI | LLM API (OpenAI) with heuristic fallback |
| Frontend | React (in-browser Babel) |
| Testing | pytest |
| Financial Representation | Integer paise (no floating-point) |

---

## Project Structure

```
Nivara/
├── backend/
│   ├── main.py              — FastAPI endpoints
│   ├── models.py            — Pydantic data models
│   ├── ingestion.py         — CSV ingestion + normalization
│   ├── linking.py           — Entity linking
│   ├── engine.py            — Deterministic reconciliation engine
│   ├── generator.py         — Synthetic data generator
│   ├── evaluation.py        — Evaluation harness
│   ├── ai_investigator.py   — LLM exception analyzer + DemoLLMClient
│   ├── batch_analyzer.py    — Cross-settlement pattern detection
│   ├── audit.py             — Append-only audit logger (SHA-256 hash chain)
│   └── mcp_client.py        — MCP client
├── frontend/
│   ├── index.html           — Dashboard UI
│   ├── App.jsx              — Main React app
│   └── components/
│       ├── UploadPanel.jsx
│       ├── ResultsTable.jsx
│       ├── ReviewQueue.jsx
│       ├── AuditTrace.jsx
│       └── BatchPatterns.jsx
├── tests/                   — 505 tests
├── data/
│   ├── demo/                — Demo dataset (80 settlements)
│   └── evaluation/          — Evaluation dataset with ground truth
├── demo.py                  — One-command demo script
└── README.md
```

---

## What We Intentionally Did Not Build

- **User authentication** — single-user demonstration environment
- **PostgreSQL** — SQLite is sufficient for demonstration workload
- **Real-time webhooks** — batch processing for MVP
- **PDF/bank-statement OCR** — CSV is the supported input format
- **Multi-currency reconciliation** — focused on INR/paise
- **LLM fine-tuning** — structured prompting and validation

These are deliberate scope decisions rather than accidental omissions.

---

## License

MIT

Built for the **Razorpay Buildathon 2026 — Track 04: AI Finance Controller**.
