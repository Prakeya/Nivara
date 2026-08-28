# NIVARA — AI Settlement Intelligence Agent

**Track:** Razorpay Buildathon 2026 — Track 04: AI Finance Controller

> **Deterministic when provable. AI when reasoning is required. Human when uncertainty remains.**

---

## Overview

Nivara is an AI-assisted financial reconciliation platform designed to reconcile Razorpay settlements against transaction, refund, and bank-credit records while maintaining strict financial safety controls.

Traditional reconciliation is often manual and time-consuming. A naïve AI approach introduces another risk: allowing an LLM to directly interpret raw financial records, calculate amounts, or approve discrepancies.

**Nivara takes the opposite approach.**

The system separates deterministic financial computation from AI-assisted investigation:

1. **Python proves what can be proven.**
2. **AI investigates what requires reasoning.**
3. **Humans make the final decision when uncertainty remains.**

The AI is deliberately constrained and can never automatically approve a financial reconciliation.

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
9. Creates structured evidence for eligible AI investigations.
10. Validates AI responses and evidence citations.
11. Escalates uncertain cases to human review.
12. Records decisions in an append-only audit trail.
13. Detects batch-level patterns.
14. Exposes the workflow through FastAPI and a React dashboard.

---

# Core Architecture

## Deterministic → AI → Human

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

The LLM is **not involved** in these calculations.

### 2. AI Investigator

AI is used only when deterministic rules cannot fully explain a discrepancy.

The AI receives a **structured evidence packet**, not raw CSV files.

The AI:

* Classifies the discrepancy
* Produces an explanation
* References supplied evidence
* Provides a confidence score

The AI cannot calculate financial amounts or modify financial records.

### 3. Human Review

Cases requiring investigation are placed into a human review queue.

The reviewer can inspect:

* Settlement details
* Deterministic checks
* Evidence
* AI explanation
* Confidence
* Audit history

The final decision remains with the human reviewer.

---

# Safety Architecture

Nivara is designed around five explicit safety guarantees.

### 1. AI Never Calculates Money

All financial arithmetic is performed by deterministic Python code using integer paise values.

### 2. AI Never Modifies Financial Records

The AI response schema does not contain financial fields such as:

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

AI recommendations are restricted to:

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

Timeouts, malformed responses, API failures, invalid evidence, and other AI failures result in:

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

If deterministic checks pass but the financial result still does not reconcile, the case can proceed to AI investigation.

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
               │          AI Investigator
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
| 7     | AI Investigator                        |
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
│   └── audit.py
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

# Generate Synthetic Evaluation Data

Generate a 60-settlement evaluation dataset:

```bash
python backend/generator.py --output data/evaluation/ --count 60
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
PYTHONPATH="$PWD" pytest tests/ -q
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

The synthetic evaluation dataset contains 60 labeled settlements:

| Ground Truth         |  Count |
| -------------------- | -----: |
| Clean Match          |     30 |
| Missing Reference    |      5 |
| Duplicate Settlement |      2 |
| Bank Mismatch        |      5 |
| Fee Mismatch         |      5 |
| Tax Inconsistency    |      3 |
| Refund Timing        |      5 |
| Unexplained          |      5 |
| **Total**            | **60** |

The ground-truth dataset allows the complete pipeline to be evaluated against known outcomes rather than relying on a manually selected demonstration case.

---

# Evaluation Metrics

Nivara evaluates the reconciliation pipeline using:

* Match rate
* False accept rate
* Safe escalation rate
* AI invocation rate
* AI auto-approval rate
* Processing time per settlement

The key safety metric is:

```text
AI Auto-Approval Rate = 0%
```

This is intentional and enforced by design.

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
* AI investigations

### 3. Inspect a Clean Match

Open a clean settlement and inspect its deterministic reconciliation trace.

### 4. Inspect an Exception

Open a fee, tax, bank, or reference exception.

The system identifies the deterministic rule that failed.

### 5. Inspect an AI Investigation

Open a discrepancy requiring reasoning.

The dashboard displays:

* Evidence
* AI classification
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
* **Microservices, Docker, and Kubernetes** — the implementation intentionally uses a lightweight architecture suitable for the buildathon and demonstration environment.

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
