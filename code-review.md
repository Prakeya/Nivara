# Nivara — Code Review

Scope: full backend (`backend/`) + frontend (`frontend/src`) + infra (alembic, docker, requirements). Reviewed 2026-09-05.

---

## 1. Critical / Correctness

### 1.1 Frontend app broken at runtime — FIXED
`frontend/src/App.jsx` used `UploadPanel`, `HeroMetrics`, `ResultsTable`, `CashFlowImpact`, `SettlementSimulator`, `AgentReasoningTree`, `SettlementRiskRadar`, `CrossSourceLinker`, `ReviewQueue`, `BatchPatterns`, `AuditTrace` as bare JSX identifiers — none imported. Component files instead did `window.X = X` at their end — leftover pre-Vite global-script pattern, never converted to ES modules. `npm run build` used to succeed while the app threw `ReferenceError` at render.

**Applied:** every component file (`AgentReasoningTree.jsx`, `AuditTrace.jsx`, `BatchPatterns.jsx`, `CashFlowImpact.jsx`, `CrossSourceLinker.jsx`, `ResultsTable.jsx`, `ReviewQueue.jsx`, `SettlementRiskRadar.jsx`, `SettlementSimulator.jsx`, `UploadPanel.jsx`) now `import React from 'react'` and `export default X` (`ResultsTable.jsx` additionally `export { HeroMetrics }`). `App.jsx` imports all ten. `window.X` globals removed. `npm run build` verified clean, no stray `window.` refs left.

### 1.2 Duplicate `/health` route shadows deep health check
`backend/main.py:670-672` registers a stub `/health` returning `{"status":"ok"}`. Starlette matches first-registered route, so the real deep check (`deep_health_check`, `main.py:865-871`) is unreachable. README claims deep DB/LLM/disk health check — it never runs.

**Fix:** remove the stub, keep the deep-check route.

### 1.3 Auth model inconsistent between endpoint families
`/api/fetch-razorpay` and `/api/reconcile-razorpay` (`main.py:983-1234`) use `verify_auth` — raw API-key match (`main.py:319-325`) — bypassing the RBAC permission model (`require_upload`, `require_review`, etc. in `rbac.py`) used by `/upload` and review endpoints. Any valid key, even VIEWER role, can trigger reconciliation (a compute/write op).

**Fix:** gate these endpoints behind the same RBAC permission checks as `/upload`.

### 1.4 Silent ground-truth evaluation failure
`main.py:529-530` — bare `except Exception: logger.warning(...)` around ground-truth JSON load + `evaluate_batch`. No distinction between "file missing" (expected) and "evaluation crashed" (bug) — both look the same in logs.

---

## 2. Security

- Broad exception swallowing with no diagnostic logging: `backend/audit.py:247,453`, `backend/secret_manager.py:52,72`, `backend/mcp_client.py:67`, `backend/database.py:34,45`. Failures vanish silently — use `logger.exception(...)` at minimum.
- `backend/rbac.py:76-77` — when `NIVARA_API_KEY` is unset, every request implicitly gets `Role.ADMIN`. Fine for a demo, but README/`.env.example` should say plainly: **"no API key configured = every caller is admin"**, not just "open access."
- `backend/database.py:29-30` (currently dead, see §3) — Postgres connection built straight from `NIVARA_DATABASE_URL` with no SSL/param validation. Flag before ever wiring it live.

---

## 3. Dead Code — remove list

| File | What | Why dead |
|---|---|---|
| `backend/main.py:670-672` | duplicate `/health` stub | shadowed real deep-health route, see §1.2 |
| `backend/database.py` (whole file, 99 lines) | Postgres/SQLite abstraction | zero imports anywhere in `backend/` or `tests/`; `AuditLogger` opens its own sqlite connection directly |
| `alembic/env.py`, `alembic/versions/001_initial.py` | migration scaffolding | `target_metadata = None`, not wired to any active schema |
| `backend/engine.py:590-639` | AI-invocation branch inside `run_engine` | both call sites (`main.py:457`, `main.py:1191`) always pass `llm_client=None`; branch unreachable in the API path — real AI flow lives in `main.py:process_reconciliation_results` |
| `main.py:509` | `ai_auto = 0  # AI never auto-approves` | hardcoded, dead field surfaced through `/status`, `/api/metrics` |
| `backend/ab_testing.py` (80 lines) | A/B model routing | zero references anywhere |
| `backend/webhooks.py` (76 lines) | outbound webhook notifier | zero references anywhere |
| `backend/tasks.py` (50 lines) | Celery/Redis job queue | zero references; matches README's own "not used" admission |
| `backend/cost_tracker.py` (121 lines) | per-settlement LLM cost tracking | zero references anywhere |
| `backend/semantic_validation.py` (92 lines) | — | zero references; also excluded from coverage/mypy |
| `backend/csv_schema.py` | `CSV_SCHEMAS` | only used by `tests/test_integration.py`; real parser `ingestion.py` never imports it |

**Dependency to drop:** `psycopg2-binary` (`requirements.txt:11`) — only consumer is orphaned `database.py`.

**Confirmed NOT dead** (verified via import grep — keep): `batch_analyzer`, `mcp_client`, `groq_client`, `ai_investigator`, `evidence_packet`, `fallback_chain`, `circuit_breaker`, `evaluation`, `health`, `linking`, `metrics`, `model_selector`, `prompt_registry`, `deterministic_guard`, `ai_validator`. `openai`, `groq`, `prometheus-client` deps are legitimate lazy/optional imports.

---

## 4. Code Quality

- `pyproject.toml:8-16,29-36` excludes `rbac.py, secret_manager.py, pii_redaction.py, webhooks.py, tasks.py, logging_config.py, semantic_validation.py` from **both** coverage and mypy strict — auth, secrets, and PII redaction are the least-verified code in the repo. Bring these under the gate (after deleting the dead ones per §3).
- `main.py` is 1318 lines — routes, in-memory job store, rate limiter, auth, Razorpay bridge, pagination all in one file. Split into routers: `routes/upload.py`, `routes/review.py`, `routes/razorpay.py`, `routes/metrics.py`.
- Response-shape drift: `/upload` handler (`main.py:492-509`) computes `unresolved` + `ai_investigations`; `/api/reconcile-razorpay` handler (`main.py:1203-1207`) omits both. Two "run reconciliation" endpoints, two different JSON shapes.
- `main.py:1082` — `from_date`/`to_date` never validated against each other before hitting Razorpay.
- `requirements.txt:6` — `pytest` bundled into prod requirements instead of a dev-only extras group; pulls test tooling into prod installs/images.

---

## 5. Testing Gaps

- 7 modules excluded from coverage/mypy (§4) have zero enforced floor — includes RBAC and PII redaction, the two modules where a silent regression is costliest.
- `engine.py:590-639` — dead in prod if untested, or tested-but-unreachable (false confidence) if it is. Needs explicit test-path audit either way.
- Frontend has **zero** test coverage of the broken-import issue (§1.1) — a basic render smoke test would have caught it.

---

## 6. Local Setup

```bash
git clone <repository-url>
cd Nivara
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
export GROQ_API_KEY=<your_key>        # required — app fails fast at startup (main.py:194-213)
# optional: RAZORPAY_API_KEY / RAZORPAY_API_SECRET, NIVARA_API_KEY, NIVARA_ROLE_<key>, NIVARA_DATABASE_URL (currently inert, see §3)
uvicorn backend.main:app --reload      # serves API + frontend/dist if built
```

Frontend dev server (separate terminal):

```bash
cd frontend
npm install
npm run dev      # vite dev server; proxies /api /upload /status /audit /health /metrics /v1 -> localhost:8000
```

Note: frontend import fix applied (§1.1) — `npm run build` clean and app now wires correctly.

Docker alternative:

```bash
docker build -t nivara .
docker compose up --build
```

---

## 7. Testing

```bash
pytest -q
pytest --cov=backend --cov-report=term -q   # fail_under = 85 (pyproject.toml:19)
python3 -m compileall -q backend
cd frontend && npm run build
```

Claimed baseline (README, not re-executed in this review): 705 tests passing, 86.46% backend coverage.

---

## Priority order to fix

1. ~~§1.1 frontend broken imports (app unusable)~~ — done
2. §1.2 duplicate `/health` route
3. §1.3 RBAC bypass on Razorpay endpoints
4. §3 delete dead files, drop `psycopg2-binary`
5. §2 add logging to swallowed exceptions
6. §4/§5 split `main.py`, bring excluded modules under coverage/mypy
