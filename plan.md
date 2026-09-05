# Nivara — Fix Plan

Source: `code-review.md`. §1.1 (frontend broken imports) already fixed — not in this plan. Order matters: correctness/security first, dead-code removal before the router split (less to move), router split before the coverage-gate phase (coverage measured against final structure).

---

## Phase 1 — Correctness fixes

### 1.1 Fix duplicate `/health` route (review §1.2)
- Remove stub `/health` route at `backend/main.py:670-672`.
- Keep `deep_health_check` route (`main.py:865-871`) as the sole `/health` handler.
- Verify: `curl localhost:8000/health` returns deep-check payload (DB/LLM/disk fields), not `{"status":"ok"}`.

### 1.2 Fix RBAC bypass on Razorpay endpoints (review §1.3)
- `/api/fetch-razorpay` and `/api/reconcile-razorpay` (`main.py:983-1234`) currently gate on `verify_auth` only.
- Add `require_upload` permission check (same as `/upload`) to both.
- Verify: request with a VIEWER-role key gets 403 on both endpoints; ADMIN/UPLOAD-role key still succeeds.
- Update `API.md` if it documents required roles per endpoint.

### 1.3 Fix silent ground-truth eval failure (review §1.4)
- `main.py:529-530`: split the bare `except Exception` into `except FileNotFoundError` (expected, keep as `logger.info`/skip) and a separate `except Exception` for real evaluation crashes (`logger.exception(...)`, keep non-fatal but loud).
- Verify: intentionally break `ground_truth.json` syntax locally, confirm the error now logs at ERROR level with traceback, not swallowed as a plain warning.

---

## Phase 2 — Security

### 2.1 Add logging to swallowed exceptions (review §2)
- Files: `backend/audit.py:247,453`, `backend/secret_manager.py:52,72`, `backend/mcp_client.py:67`, `backend/database.py` — **skip `database.py`, deleted in Phase 3**.
- Replace bare/broad `except Exception: pass`-style blocks with `logger.exception("<context>")` before continuing/re-raising as currently designed. No behavior change, just visibility.

### 2.2 Document the no-auth-means-admin behavior (review §2)
- `backend/rbac.py:76-77` behavior is correct, just undocumented plainly.
- Update `README.md` and `.env.example` comment near `NIVARA_API_KEY` to state explicitly: "If `NIVARA_API_KEY` is unset, every caller is treated as ADMIN — do not deploy without setting it."

---

## Phase 3 — Dead code removal (review §3)

Delete, in one commit:
- `backend/database.py`
- `alembic/` (both `env.py` and `versions/001_initial.py`; drop the directory)
- `backend/ab_testing.py`
- `backend/webhooks.py`
- `backend/tasks.py`
- `backend/cost_tracker.py`
- `backend/semantic_validation.py`
- `backend/csv_schema.py`
- `backend/engine.py:590-639` (dead AI-invocation branch inside `run_engine`) — remove the branch, keep the early-return path that's actually used; simplify `run_engine`'s signature if `llm_client` param becomes unused
- `main.py:509` — remove hardcoded `ai_auto` field and its exposure in `/status`/`/api/metrics` responses (also update `API.md` schema docs if that field is documented)

Also:
- Remove `psycopg2-binary` from `requirements.txt`.
- Remove `alembic.ini` (root) since `alembic/` is gone.
- Move `pytest` (and `pytest-cov` if present) out of `requirements.txt` into a new `requirements-dev.txt` or a `[project.optional-dependencies]` dev extra in `pyproject.toml`.

Verify after each deletion: `python3 -m compileall -q backend`, `pytest -q` full suite green, `grep -rn "csv_schema\|from backend.database\|from backend import database" backend/ tests/` returns nothing except the test file being adjusted (see below).

**Caveat:** `backend/csv_schema.py` is referenced by `tests/test_integration.py`. Before deleting, check what that test actually asserts — either delete/rewrite the test alongside, or inline the schema constants directly into the test file if still needed there. Flag this to me if the test does something non-trivial with it.

---

## Phase 4 — `main.py` router split (review §4)

Current: 1318 lines, routes + in-memory job store + rate limiter + auth + Razorpay bridge + pagination all in one file.

Target structure:
```
backend/
  main.py              # app init, middleware, startup/shutdown, router mounting only
  job_store.py         # in-memory job store + rate limiter (extracted, no route handlers)
  routes/
    __init__.py
    upload.py           # POST /upload
    status.py           # GET /status/{job_id}
    audit.py             # GET /audit/{upload_hash}, /audit/{upload_hash}/verify, /settlement/{id}
    review.py            # POST /api/review/{id}/decision, GET /api/review/pending, GET /api/review/{id}
    razorpay.py          # POST /api/fetch-razorpay, POST /api/reconcile-razorpay
    metrics.py           # GET /api/metrics, GET /metrics
    health.py            # GET /health
```
- Each router file: `APIRouter()` instance, route handlers moved verbatim (logic unchanged — this is a structural move, not a rewrite).
- `main.py` becomes: create `FastAPI()`, mount each router with its prefix, keep global exception handlers/middleware/startup checks (e.g. the `GROQ_API_KEY` fail-fast check).
- Shared state (job store, rate limiter instance) becomes a module-level singleton in `job_store.py`, imported by whichever routers need it — avoid circular imports by keeping `job_store.py` dependency-free of the route files.
- Fix response-shape drift while moving code: `/api/reconcile-razorpay` handler (moving into `routes/razorpay.py`) should compute `unresolved` + `ai_investigations` the same way `/upload`'s handler (`routes/upload.py`) does — extract that computation into one shared helper (e.g. `job_store.py` or a new `response_shaping.py`) both call.
- Add `from_date <= to_date` validation in `routes/razorpay.py` before calling Razorpay fetch (400 on violation).

Verify: full `pytest -q` green after split (tests currently import from `backend.main` — check `tests/` for `from backend.main import app` or handler-level imports and update paths as needed), `python3 -m compileall -q backend`, manually hit each endpoint via `curl` or existing integration tests to confirm no route regressions, response shapes now match between `/upload` and `/api/reconcile-razorpay`.

This phase touches every route — do it as its own commit, run the full suite before moving to Phase 5.

---

## Phase 5 — Coverage/mypy gate expansion (review §5)

Scope after Phase 3 deletions: `webhooks.py`, `tasks.py`, `semantic_validation.py` are gone, so only 4 modules remain excluded in `pyproject.toml:8-16,29-36`:
- `rbac.py`, `secret_manager.py`, `pii_redaction.py`, `logging_config.py`

Steps:
1. Remove these 4 from the `omit` list (`pyproject.toml:8-16`) and the mypy `exclude` list (`pyproject.toml:29-36`) — one module at a time, not all at once.
2. For each module: run `mypy backend/<module>.py` (strict), fix type errors (add annotations, fix `Any` leaks, handle `Optional` narrowing) without changing runtime behavior.
3. Run `pytest --cov=backend --cov-report=term -q` after adding the module back to coverage scope; write tests to close gaps until the module clears the existing `fail_under = 85` threshold. Prioritize: `rbac.py` (permission-check branches, the no-key-means-admin path from Phase 2.2) and `pii_redaction.py` (redaction correctness — the actual PII-scrubbing logic) get the most scrutiny since they're the highest-risk modules per the review.
4. `secret_manager.py` and `logging_config.py` get standard unit coverage — no special scrutiny needed beyond hitting the `fail_under` bar.

Verify: `pytest --cov=backend --cov-report=term -q` passes with all 7 originally-excluded-minus-3-deleted modules now included, `mypy backend --strict` (or whatever the actual invocation is — check if there's a mypy CI step) passes with zero exclusions left in `pyproject.toml`.

---

## Execution order & checkpoints

1. Phase 1 (correctness) → run `pytest -q` → commit
2. Phase 2 (security/logging) → run `pytest -q` → commit
3. Phase 3 (dead code delete) → run `pytest -q` + `compileall` → commit
4. Phase 4 (router split) → run full `pytest -q` + manual endpoint smoke test → commit
5. Phase 5 (coverage/mypy gate) → run `pytest --cov` + `mypy` → commit

Each phase is a separate commit so a regression is bisectable. Stop and flag me if any phase surfaces something the review didn't predict (e.g. a test that breaks in a way that suggests hidden behavior dependency on dead code).
