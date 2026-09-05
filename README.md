# Nivara

> Settlement reconciliation for Razorpay Standard Checkout — deterministic math, advisory AI, human decides.

[![Track](https://img.shields.io/badge/Track-04%20AI%20Finance%20Controller-blue)](#)
[![Tests](https://img.shields.io/badge/Tests-763%20passing-success)](#-evaluation--results)
[![Coverage](https://img.shields.io/badge/Backend%20Coverage-89%25-brightgreen)](#-evaluation--results)
[![Match Rate](https://img.shields.io/badge/Match%20Rate-87.5%25-orange)](#-evaluation--results)

**Track 04 — AI Finance Controller** · Razorpay Buildathon 2026

[Demo Video](PASTE_VIDEO_URL_HERE) · [Repo](PASTE_REPO_URL_HERE)

---

## TL;DR

Nivara reconciles Razorpay settlements against transactions, refunds, and bank credits — either from 4 uploaded CSVs or a live Razorpay sandbox pull. It runs **12 deterministic checks** in integer paise (no floating-point money math), and only calls an LLM for the cases the math genuinely can't explain.

- **12** deterministic reconciliation checks
- **0%** AI auto-approval rate — schema-enforced, not a policy promise
- **87.5%** match rate on an 80-settlement evaluation set (2 disclosed blind spots)
- **763** tests passing, **89%** backend coverage
- **~36,000** in-memory reconciliations/sec (pure compute, no I/O)

The AI never touches money. It classifies *why* a discrepancy exists — the deterministic engine still decides *whether* one exists.

---

## The Problem

Merchants reconciling Razorpay settlements have to cross-check transactions, refunds, settlement reports, and bank credits by hand — references, fees, taxes, UTRs, and arithmetic, across four different data sources that don't always agree.

Most "AI reconciliation" demos solve this by handing raw rows to an LLM and trusting whatever comes back. That's fast to build and impossible to audit.

Nivara's bet: **prove what can be proven with code, and only ask AI to explain what code can't.**

| Naive LLM wrapper | Nivara |
|---|---|
| Sends raw CSV rows to the model | Sends structured, cited evidence packets |
| LLM computes amounts | Python computes every amount, in integer paise |
| LLM can approve a transaction | LLM's only possible action is `ESCALATE_TO_HUMAN` |
| Explanations are unverifiable | Every citation is checked against the evidence packet |
| No measured accuracy | 80-settlement labeled evaluation set, published match rate |

---

## How It Works

```
CSV upload (4 files)  ──┐
                         ├──▶ Ingest + validate ──▶ Deterministic engine (12 checks, integer paise)
Razorpay live fetch  ───┘                                        │
                                                                   ▼
                                                     ┌── clean / exception ──▶ done
                                                     │
                                                     └── MATH_DISCREPANCY
                                                              │
                                                              ▼
                                              Deterministic guard (only this state may ask AI)
                                                              │
                                                              ▼
                                        Groq 70B → 8B fallback, evidence-only prompt
                                                              │
                                                              ▼
                                    Citation validation — hallucinated evidence is rejected
                                                              │
                                                              ▼
                                          Always escalates ──▶ Human review queue ──▶ decision
                                                              │
                                                              ▼
                                          SHA-256 hash-chained audit record (PII redacted)
```

1. **Ingest** — 4 CSVs (transactions, settlements, refunds, bank credits) or a live Razorpay sandbox pull via `/api/reconcile-razorpay`, with settlement-derived fallback rows when the sandbox doesn't expose full linkage.
2. **Deterministic engine** — 12 checks in integer paise: references, linkage, fees, taxes, bank credits, UTRs, settlement arithmetic.
3. **Guard** — only `MATH_DISCREPANCY` cases (math that doesn't resolve) are eligible for AI. Clean matches and clear exceptions never reach the model.
4. **AI classification** — Groq 70B primary, 8B fallback, given a structured evidence packet (`EvidencePacketV2`), not raw rows.
5. **Citation validation** — every fact the model cites must exist in the evidence packet; invented citations get the response rejected.
6. **Human review** — the model's only possible recommendation is escalation. A human makes the actual call, through `/api/review/{id}/decision`.
7. **Audit** — every batch and every decision is written to a SHA-256 hash-chained, append-only log, with PII redacted before persistence.

---

## AI Judgment

**Where AI is used:** classifying *why* a `MATH_DISCREPANCY` exists — timing mismatch, refund timing, or genuinely unexplained — from a structured evidence packet.

**Where AI is deliberately not used:** arithmetic, currency amounts, linkage checks, fee/tax verification, approval decisions. All of that is Python, integer paise, and 12 explicit rules.

**Enforced, not promised:**
- The `AIResponse` schema is `extra="forbid"` — the model cannot inject new fields.
- The only valid `recommended_action` is `ESCALATE_TO_HUMAN`. There is no `APPROVE` value in the schema for the model to reach for.
- `validate_citations()` checks every cited evidence ID against the actual evidence packet; a hallucinated citation gets the response thrown out and the case marked unresolved.
- Any LLM failure — timeout, malformed response, hallucination — becomes `UNRESOLVED` + `escalate_to_human=True`. The system never silently assumes a discrepancy is fine.

---

## Evaluation & Results

Measured against an 80-settlement synthetic evaluation set spanning 11 edge-case categories.

| Metric | Result |
|---|---:|
| Evaluation settlements | **80** |
| Match rate | **87.5%** |
| False accept rate (disclosed blind spots) | **12.5%** (10 settlements, 2 known categories) |
| Per-class macro F1 | **0.82** |
| Deterministic throughput | **~36,000 reconciliations/sec** (in-memory, no I/O) |
| AI auto-approval rate | **0%** (schema-enforced) |
| Test suite | **763 passing** |
| Backend coverage | **89%** |

The dataset is synthetic and co-designed with the engine — a 100% match rate would mean the engine only catches what it was built to catch, which would be a red flag, not a win. 87.5% with disclosed blind spots is the honest number.

**Two known blind spots, disclosed on purpose:** `refund_after_settlement` and `timing_race` — cases that look clean to the deterministic engine but carry a real exception. These need live LLM investigation or additional business rules the current scope doesn't implement. We'd rather name them than hide them.

---

## Architecture

FastAPI backend, React/Vite frontend. The backend recently went through a router split — one 1300-line `main.py` became app wiring + focused modules:

```
backend/
├── main.py              # FastAPI app, middleware, startup checks, router mounting
├── job_store.py          # in-memory job store + rate limiter (dependency-free)
├── response_shaping.py   # shared result-summary computation (upload + live-fetch use the same shape)
├── api_helpers.py        # helpers shared across route modules
├── engine.py              # 12 deterministic checks
├── ai_investigator.py     # Groq investigation + citation flow
├── ai_validator.py        # response/citation validation
├── deterministic_guard.py # decides which cases may reach AI at all
├── rbac.py                 # upload/review/read/configure permissions
├── audit.py                 # SHA-256 hash-chained audit log
├── pii_redaction.py         # redaction before persistence
└── routes/
    ├── upload.py           # POST /upload
    ├── status.py            # GET /status/{job_id}
    ├── audit.py              # GET /audit/{upload_hash}, /verify, /settlement/{id}
    ├── review.py              # human review queue + decisions
    ├── razorpay.py             # live fetch + reconcile, RBAC-gated
    ├── metrics.py               # /api/metrics, /metrics (Prometheus)
    ├── health.py                 # deep health check (DB, LLM, disk)
    ├── v1.py                      # versioned sub-API
    └── frontend.py                 # static + SPA serving
```

### Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, Uvicorn, Pydantic v2 |
| AI | Groq SDK (70B/8B), optional OpenAI fallback path |
| Persistence | SQLite audit log (hash-chained), optional PostgreSQL branch |
| Metrics | JSON + optional Prometheus exposition |
| Frontend | React 18, Vite, Recharts |
| Tests | pytest, pytest-cov, mypy (strict on RBAC/secrets/PII modules) |

---

## What Broke — And How We Recovered

Real issues found and fixed during a structured review-and-fix pass on this codebase, not hypothetical ones.

**1. The frontend shipped broken.**
`App.jsx` referenced ten components that were never imported — the component files assigned themselves to `window.X` instead, a leftover pre-Vite pattern. `npm run build` passed clean because nothing checks for undefined JSX identifiers at build time; the app threw `ReferenceError` at first render. Fixed by converting every component to a real ES module export and importing them properly.

**2. The health endpoint was lying.**
Two `/health` routes existed. The first-registered one — a stub returning `{"status": "ok"}` — silently shadowed the real deep check that verifies DB, LLM reachability, and disk. The API always reported healthy regardless of actual state. Removed the stub; the deep check is now the only `/health` handler.

**3. A live-money endpoint skipped the permission system.**
`/api/reconcile-razorpay` and `/api/fetch-razorpay` checked for *a* valid API key, but not *which permission* it had — bypassing the RBAC layer `/upload` already enforced. A viewer-level key could trigger a live reconciliation run. Closed the gap, added tests proving 403 for viewer-level keys and pass-through for upload-permitted ones.

**4. A fix introduced a quieter bug while closing a louder one.**
Adding missing exception logging to three modules used `logging.getLogger(__name__)`, which put the new logs outside the app's `nivara.*` namespace — invisible to the JSON-formatted logging pipeline in production. Caught in code review before merge, not after.

**5. The router split didn't lose anything.**
Splitting `main.py` into 8 route modules touched every endpoint in the app. Verified before merge: all 18 original routes present, zero circular imports, and a response-shape inconsistency between `/upload` and `/api/reconcile-razorpay` fixed as part of the same pass — both now return the same result fields via one shared helper.

Every fix above shipped with a regression test and a full green suite (763 tests) before merge.

---

## Current Scope & Limitations

- Standard Checkout settlements only — RazorpayX payouts, Smart Collect virtual accounts, and Route split settlements are not implemented.
- The active audit path is SQLite; a PostgreSQL branch exists in the codebase but isn't the production-selected backend.
- The job store is in-memory — a server restart loses in-flight job state (the audit trail itself persists in SQLite).
- Redis/Celery modules exist as optional code, not wired into the synchronous API path.
- Two disclosed blind spots in the evaluation set (`refund_after_settlement`, `timing_race`) that the deterministic engine cannot currently catch.

---

## What We'd Build Next

1. **Persistent job queue** (Redis/SQS) — removes the in-memory job-store restart risk.
2. **Async batch processing** — for large uploads, decoupled from the request/response cycle.
3. **Multi-tenant isolation** — current design is single-tenant; production needs per-tenant audit trails and access control.
4. **Close the two disclosed blind spots** — likely needs targeted business rules, not just more LLM calls.

None of these affect the core reconciliation logic or the AI-advisory-only guarantee — they're infrastructure the hackathon scope deliberately left out.

---

## Running Locally

```bash
git clone PASTE_REPO_URL_HERE
cd Nivara
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
export GROQ_API_KEY=your_key   # required — app fails fast at startup without it
uvicorn backend.main:app --reload
```

Frontend (separate terminal):

```bash
cd frontend
npm install
npm run dev
```

Tests:

```bash
pytest -q
pytest --cov=backend --cov-report=term -q
```

> **Security note:** if `NIVARA_API_KEY` is left unset, every caller is treated as admin — fine for local demo, not for anything reachable by untrusted callers.

---

## Demo

**[Watch the demo →](PASTE_VIDEO_URL_HERE)**

![Upload flow](PASTE_SCREENSHOT_PATH_HERE)
![Dashboard / results](PASTE_SCREENSHOT_PATH_HERE)
![Human review queue](PASTE_SCREENSHOT_PATH_HERE)
![Audit trail](PASTE_SCREENSHOT_PATH_HERE)

Covers: the reconciliation problem, live upload → deterministic engine → AI classification → human review → audit trail, and the measured evaluation results above.

---

## Track 04 Alignment

| Requirement | Nivara |
|---|---|
| AI Finance Controller | ✅ Deterministic engine decides, AI classifies exceptions only |
| Measured accuracy | ✅ 87.5% match rate, disclosed blind spots |
| Throughput | ✅ ~36,000 reconciliations/sec (deterministic path) |
| AI judgment shown honestly | ✅ 0% auto-approval, schema-enforced, citation-validated |
| Failure recovery documented | ✅ See "What Broke" above |
| Audit trail | ✅ SHA-256 hash-chained, PII-redacted |

---

## Submission

**Track:** Track 04 — AI Finance Controller
**Repository:** PASTE_REPO_URL_HERE
**Demo:** PASTE_VIDEO_URL_HERE

---

> We didn't optimize for a perfect demo. We optimized for a reconciliation engine that knows the difference between "this matches" and "I'm not sure" — and never confuses the two.
