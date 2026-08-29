# NIVARA — Settlement Intelligence Agent

**Track:** Razorpay Buildathon 2026 — Track 04: AI Finance Controller

> **Deterministic when provable. LLM when reasoning is required. Human when uncertainty remains.**

Zero auto-approvals by design. All financial decisions are deterministic or escalated to human review.

---

## Overview

Nivara is an LLM-assisted financial reconciliation platform designed to reconcile Razorpay settlements against transaction, refund, and bank-credit records while maintaining strict financial safety controls.

Traditional reconciliation is often manual and time-consuming. A naive approach of sending raw data to an LLM introduces risks: allowing an LLM to directly interpret raw financial records, calculate amounts, or approve discrepancies.

**Nivara takes the opposite approach.**

The system separates deterministic financial computation from LLM-assisted investigation:

1. **Python proves what can be proven.**
2. **LLM classifies what requires reasoning.**
3. **Humans make the final decision when uncertainty remains.**

The LLM is deliberately constrained and can never automatically approve a financial reconciliation.

---

## The Problem

A merchant may receive several independent financial data sources:

* Payment transactions
* Razorpay settlement records
* Refund records
* Bank statement credits

Reconciliation requires determining:

* Whether all settlement payments are correctly linked
* Whether refunds are accounted for
* Whether fees are correct
* Whether tax is correct
* Whether the expected settlement amount reached the bank
* Whether UTRs match
* Whether discrepancies can be explained
* Whether an unresolved case should be escalated

Nivara automates these checks while keeping financial computation outside the LLM.

---

## What Nivara Does

Nivara processes four CSV sources:

| File               | Purpose                     |
| ------------------ | --------------------------- |
| `transactions.csv` | Payment transaction records |
| `refunds.csv`      | Refund records              |
| `settlements.csv`  | Razorpay settlement records |
| `bank_credits.csv` | Bank statement credits      |

The system:

1. Validates incoming CSV data.
2. Normalizes financial values and dates.
3. Detects duplicates.
4. Resolves payment, refund, and settlement references.
5. Links settlements to bank credits.
6. Runs deterministic reconciliation rules.
7. Calculates expected and actual amounts using integer paise.
8. Identifies deterministic exceptions.
9. Creates structured evidence for eligible LLM exception analysis.
10. Validates LLM responses and evidence citations.
11. Escalates uncertain cases to human review.
12. Records decisions in an append-only audit trail.
13. Detects batch-level patterns.
14. Exposes the workflow through FastAPI and a React dashboard.

---

# Core Architecture

## Deterministic → LLM → Human

### 1. Deterministic Reconciliation Engine

The deterministic engine handles all financial computation.

It validates:

* References
* Duplicate records
* Settlement/payment relationships
* Refund relationships
* Fee calculations
* Tax calculations
* Bank-credit existence
* UTR consistency
* Amount consistency
* Expected settlement amount
* Settlement difference
* Adjustment consistency (declared adjustments must bridge the gap)

The LLM is **not involved** in these calculations.

### 2. LLM Exception Analyzer

LLM is used only when deterministic rules cannot fully explain a discrepancy.

The LLM receives a **structured evidence packet**, not raw CSV files.

The LLM:

* Classifies the discrepancy
* Produces an explanation
* References supplied evidence
* Provides a confidence score

The LLM cannot calculate financial amounts or modify financial records.

### 3. Human Review

Cases requiring investigation are placed into a human review queue.

The reviewer can inspect:

* Settlement details
* Deterministic checks
* Evidence
* LLM explanation
* Confidence
* Audit history

The final decision remains with the human reviewer.

---

# Safety Architecture

Nivara is designed around five explicit safety guarantees.

### 1. AI Never Calculates Money

All financial arithmetic is performed by deterministic Python code using integer paise values.

### 2. AI Never Modifies Financial Records

The LLM response schema does not contain financial fields such as:

```text
amount
fee
refund
tax
expected_amount
actual_amount
```

Unexpected fields are rejected.

### 3. AI Never Auto-Approves

LLM recommendations are restricted to:

```text
ESCALATE_TO_HUMAN
```

The system also enforces:

```text
auto_approved_by_ai == 0
```

### 4. AI Claims Require Evidence

AI explanations must reference evidence identifiers supplied by the evidence packet.

Unsupported evidence is rejected.

### 5. AI Failures Fail Safely

Timeouts, malformed responses, API failures, invalid evidence, and other LLM failures result in:

```text
UNRESOLVED
+
HUMAN REVIEW
```

The system does not silently approve uncertain financial cases.

---

# Reconciliation Rules

## Fee Structure

The fee structure is frozen as follows:

| Payment Method | Fee                           |
| -------------- | ----------------------------- |
| UPI            | `0`                           |
| Card           | `floor(amount × 0.02) + 100`  |
| Netbanking     | `floor(amount × 0.015) + 100` |

## Tax

```text
tax = floor(fee × 0.18)
```

Fees are never refunded.

All financial calculations use integer paise arithmetic.

Floating-point arithmetic is not used for financial amounts.

## Expected Amount

For each settlement:

```text
Expected Amount
    =
    Sum(Payments)
    - Sum(Refunds)
    - Sum(Fees)
    - Sum(Tax)
```

The reconciliation difference is:

```text
Difference = Actual Amount - Expected Amount
```

A settlement becomes:

```text
CLEAN_MATCH
```

when all deterministic checks pass and:

```text
Difference == 0
```

If deterministic checks fail, a deterministic exception is recorded.

If deterministic checks pass but the financial result still does not reconcile, the case can proceed to LLM exception analysis.

---

# High-Level Architecture

```text
                    CSV Upload
                        │
                        ▼
                Schema Validation
                        │
                        ▼
                  Normalization
                        │
                        ▼
                   Entity Linking
                        │
                        ▼
            Deterministic Reconciliation
                        │
               ┌────────┴────────┐
               │                 │
               ▼                 ▼
          CLEAN_MATCH        DISCREPANCY
               │                 │
               │                 ▼
               │          LLM Exception Analyzer
               │                 │
               │       Structured Evidence
               │                 │
               │       ┌─────────┼─────────┐
               │       ▼         ▼         ▼
               │   EXPLAINED  REVIEW   UNRESOLVED
               │                 │         │
               └─────────┬───────┴─────────┘
                         ▼
                  Human Review Queue
                         │
                         ▼
                    Audit Trail
                         │
                         ▼
                      Dashboard
```

---

# Implementation Phases

| Phase | Component                              |
| ----- | -------------------------------------- |
| 1     | Strict Pydantic Data Models            |
| 2     | CSV Ingestion and Normalization        |
| 3     | Entity Linking                         |
| 4     | Deterministic Reconciliation Engine    |
| 5     | Synthetic Evaluation Data Generator    |
| 6     | Evaluation Harness                     |
| 7     | LLM Exception Analyzer                |
| 8     | Batch-Level Pattern Analysis           |
| 9     | Append-Only Audit Logger               |
| 10    | FastAPI API                            |
| 11    | React Frontend                         |
| 12    | End-to-End Testing and Demo Validation |

---

# Technology Stack

| Layer                    | Technology    |
| ------------------------ | ------------- |
| Language                 | Python 3.11+  |
| API                      | FastAPI       |
| Validation               | Pydantic v2   |
| Data Processing          | Python / CSV  |
| Database                 | SQLite        |
| AI                       | LLM API       |
| Frontend                 | React         |
| Testing                  | pytest        |
| Financial Representation | Integer paise |

---

# Project Structure

```text
Nivara/
│
├── backend/
│   ├── __init__.py
│   ├── main.py
│   ├── models.py
│   ├── ingestion.py
│   ├── linking.py
│   ├── engine.py
│   ├── generator.py
│   ├── evaluation.py
│   ├── ai_investigator.py
│   ├── batch_analyzer.py
│   ├── audit.py
│   └── mcp_client.py
│
├── frontend/
│   ├── index.html
│   ├── App.jsx
│   └── components/
│       ├── UploadPanel.jsx
│       ├── ResultsTable.jsx
│       ├── ReviewQueue.jsx
│       ├── AuditTrace.jsx
│       └── BatchPatterns.jsx
│
├── tests/
│   ├── conftest.py
│   ├── test_models.py
│   ├── test_ingestion.py
│   ├── test_linking.py
│   ├── test_engine.py
│   ├── test_generator.py
│   ├── test_evaluation.py
│   ├── test_ai_investigator.py
│   ├── test_batch_analyzer.py
│   ├── test_audit.py
│   ├── test_api.py
│   ├── test_frontend.py
│   ├── test_mcp_client.py
│   └── test_e2e.py
│
├── data/
│   ├── demo/
│   └── evaluation/
│
├── requirements.txt
├── architecture.md
├── project.md
└── README.md
```

---

# Installation

## Prerequisites

Python 3.11 or newer.

Install dependencies:

```bash
pip install -r requirements.txt
```

---

# One-Command Demo

Run the full pipeline end-to-end:

```bash
python3 demo.py
```

This will:
1. Generate 80 synthetic settlements with ground truth
2. Run the full test suite (482 tests)
3. Ingest → Link → Reconcile → AI Investigate → Evaluate
4. Print match rate, per-class F1, and throughput

---

# Generate Synthetic Evaluation Data

Generate an 80-settlement evaluation dataset:

```bash
python backend/generator.py --output data/evaluation/ --count 80
```

This creates:

```text
transactions.csv
settlements.csv
refunds.csv
bank_credits.csv
ground_truth.json
```

The dataset contains known ground-truth cases.

---

# Run the Backend

```bash
uvicorn backend.main:app --reload
```

The application can then be accessed through the configured local frontend/API.

---

# Run Tests

Run the complete test suite:

```bash
pytest tests/ -q
```

The test suite covers:

* Strict data-model validation
* Financial type validation
* CSV ingestion
* Encoding normalization
* Duplicate detection
* Referential integrity
* Entity linking
* Fee validation
* Tax validation
* Reconciliation logic
* Synthetic data generation
* Evaluation metrics
* AI safety behavior
* Batch pattern detection
* Audit logging
* API behavior
* End-to-end processing

---

# Evaluation Dataset

The synthetic evaluation dataset contains **80 labeled settlements** across 11 edge-case categories:

| Ground Truth             | Count | Engine Behavior                                       |
| ------------------------ | ----- | ----------------------------------------------------- |
| Clean Match              |    30 | Correctly identified as clean                         |
| Missing Reference        |     5 | Caught — DET-EXCEPTION (reference_existence)          |
| Bank Mismatch            |     5 | Caught — DET-EXCEPTION (bank_credit_existence)        |
| Fee Mismatch             |     5 | Caught — DET-EXCEPTION (fee_validation)               |
| Tax Inconsistency        |     3 | Caught — DET-EXCEPTION (tax_validation)               |
| Refund Timing            |     5 | Caught — MATH_DISCREPANCY (detected by LLM layer)      |
| Adjustment Entry         |     5 | Caught — MATH_DISCREPANCY (amount differs from expected) |
| Partial Settlement       |     4 | Caught — DET-EXCEPTION (amount_cross_check)           |
| Refund After Settlement  |     5 | **Missed** — engine blind spot (CLEAN_MATCH)          |
| Timing Race              |     5 | **Missed** — engine blind spot (CLEAN_MATCH)          |
| Unexplained              |     8 | Caught — MATH_DISCREPANCY (all checks pass, diff≠0)   |
| **Total**                | **80** |                                                      |

**11 edge-case categories** include 4 that the engine cannot catch (refund_after_settlement, timing_race) — these are deliberate blind spots that expose where the deterministic engine needs LLM or additional checks.

---

# Evaluation Methodology & Limitations

## How We Evaluate

Nivara uses a **confusion matrix** approach against known ground-truth labels:

* **True Positive (TP):** clean_match ground truth → CLEAN_MATCH decision
* **True Negative (TN):** exception ground truth → exception decision (any non-clean)
* **False Positive (FP):** clean_match ground truth → exception decision (over-escalated)
* **False Negative (FN):** exception ground truth → CLEAN_MATCH decision (missed!)

**Match Rate** = (TP + TN) / Total
**False Accept Rate** = FN / Total
**Per-class Precision/Recall/F1** is reported for every label category.

## Honest Limitations

**The dataset is synthetic and co-designed with the engine.** The generator produces edge cases that map to the engine's 11 deterministic checks. A 100% match rate on this dataset would mean the engine catches every case it was built to catch — which is expected, not impressive.

**The match rate is deliberately NOT 100%.** We included two categories of engine blind spots:

* **refund_after_settlement:** A refund processed after settlement is not in `linked_refund_ids`. The engine computes expected = payments - refunds - fees - tax, finds difference == 0, and returns CLEAN_MATCH. But the merchant was overpaid.
* **timing_race:** A refund created during the settlement processing window is not linked. Same outcome — engine says clean, but the refund should have been deducted.

These false negatives are **honest** — they show where the deterministic engine needs LLM exception analysis or additional checks.

**What a real-world dataset would include:** partial settlements across multiple payouts, multi-currency transactions, adjustments and chargebacks, ambiguous timing edge cases, and records from multiple merchants with overlapping payment IDs. Our synthetic data does not capture this complexity.

**Throughput is measured, not estimated.** The evaluation pipeline instruments wall-clock time and reports settlements/second.

---

# Evaluation Metrics

Nivara evaluates the reconciliation pipeline using:

* **Match rate** (confusion-matrix based, not self-reported)
* **False accept rate** (exceptions missed as clean)
* **Per-class precision / recall / F1** (for every label category)
* **Macro-averaged F1** (single-number summary across all classes)
* **Escalation breakdown** (deterministic exceptions vs unresolved vs AI-reviewed)
* **Throughput** (settlements/second, instrumented)
* **AI auto-approval rate** (= 0% by design, enforced by schema)

The key safety metric is:

```text
AI Auto-Approval Rate = 0%
```

This is intentional and enforced by Pydantic schema (`AIResponse.recommended_action` is `Literal[ESCALATE_TO_HUMAN]`).

---

# API

The FastAPI layer exposes the reconciliation workflow.

Typical operations include:

```text
GET  /health
POST /upload
GET  /status/{job_id}
GET  /settlement/{settlement_id}
GET  /audit/{upload_hash}
```

The exact API behavior is implemented in `backend/main.py`.

---

# Demo Workflow

### 1. Upload

Upload the four CSV files:

```text
transactions.csv
settlements.csv
refunds.csv
bank_credits.csv
```

### 2. Dashboard

Review:

* Total settlements
* Clean matches
* Exceptions
* Unresolved cases
 * LLM investigations

### 3. Inspect a Clean Match

Open a clean settlement and inspect its deterministic reconciliation trace.

### 4. Inspect an Exception

Open a fee, tax, bank, or reference exception.

The system identifies the deterministic rule that failed.

### 5. Inspect an LLM Exception Analysis

Open a discrepancy requiring reasoning.

The dashboard displays:

* Evidence
* LLM classification
* Explanation
* Confidence
* Escalation status

### 6. Human Review

The reviewer makes the final decision.

### 7. Audit

The action and reconciliation state remain available through the audit trail.

---

# Why Nivara Is Not Just an LLM Wrapper

| Conventional AI Reconciliation           | Nivara                                                   |
| ---------------------------------------- | -------------------------------------------------------- |
| Sends raw CSV to an LLM                  | Sends structured evidence                                |
| LLM calculates financial values          | Python performs financial calculations                   |
| AI decides whether money matches         | Deterministic engine determines mathematical correctness |
| AI can generate unsupported explanations | Evidence citations are validated                         |
| AI may approve automatically             | AI always escalates to human                             |
| Single demonstration example             | Ground-truth evaluation dataset                          |
| Failures may produce guesses             | Failures become unresolved cases                         |

---

# What We Intentionally Did Not Build

The following capabilities are outside the current MVP scope:

* **User authentication** — single-user demonstration environment.
* **PostgreSQL** — SQLite is sufficient for the target demonstration workload.
* **Real-time webhooks** — the current implementation uses batch processing.
* **Email/Slack notifications** — human review is handled through the application.
* **PDF/bank-statement OCR** — CSV is the supported input format.
* **Multi-currency reconciliation** — currently focused on INR/paise.
* **Advanced chart visualizations** — tabular reconciliation views are sufficient for the MVP.
* **LLM fine-tuning** — AI behavior uses structured prompting and validation.
* **Persistent job queue** — in-memory job store for demonstration; production would need Redis/SQS.
* **Multi-tenant support** — single-tenant design for hackathon scope.

These are deliberate scope decisions rather than accidental omissions.

---

# Limitations

Nivara is optimized for a controlled batch-reconciliation environment.

A production deployment would require additional infrastructure and controls around:

* Authentication and authorization
* Production database infrastructure
* Secrets management
* Rate limiting
* Observability
* High availability
* Distributed processing
* Regulatory compliance
* Multi-currency support
* Production notification infrastructure

The current implementation prioritizes correctness, transparency, auditability, and safe AI behavior.

---

# Safety-First Design Philosophy

> **In financial systems, the AI must know when it doesn't know.**

Nivara does not optimize for maximum automation at any cost.

It prioritizes:

**Correctness over convenience.**

**Evidence over speculation.**

**Deterministic computation over probabilistic arithmetic.**

**Human oversight over automatic approval.**

The most important behavior is not explaining every discrepancy.

It is knowing when a discrepancy cannot be safely explained.

When Nivara cannot establish a trustworthy explanation, it does not guess.

**It escalates.**

That refusal is not a failure.

**It is the safety feature.**

---

# License

MIT

Built for the **Razorpay Buildathon 2026 — Track 04: AI Finance Controller**.
