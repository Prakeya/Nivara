# Nivara — Fix Plan

Source: `code-review.md`. Review §1.1 (frontend broken imports) is already fixed on this branch — not a task here.

## Global Constraints

- Run from the repo root of this worktree. Python venv already exists at `.venv/` (created with `uv venv`); use `.venv/bin/python` / `.venv/bin/pytest`, do not recreate it.
- After every task: run `.venv/bin/python -m pytest -q` (or a targeted subset while iterating, full suite before reporting DONE) and `.venv/bin/python -m compileall -q backend`. Both must be clean (ignore the pre-existing `tests/test_metrics_cov.py` prometheus `DuplicateTimeseries` failures — 7 tests, environment/registry-singleton issue unrelated to this plan, already failing on a clean checkout before any task starts. Do not attempt to fix them unless a task explicitly touches `backend/metrics.py`).
- Commit at the end of each task with a Conventional-Commits-style message (`fix:`, `refactor:`, `chore:`, `docs:` as appropriate). One commit per task, not per file.
- Never touch `frontend/` — the only frontend issue (review §1.1) is already fixed.
- Tasks run in order: 1-5 are independent of each other and touch disjoint code (safe to do in listed order but do not skip ahead of unfinished ones since later tasks assume earlier fixes exist in main.py). Task 6 (dead code) must complete before Task 7 (router split — less to move). Task 7 must complete before Task 8 (coverage measured against final file structure).

---

## Task 1: Fix duplicate `/health` route

Review finding §1.2: `backend/main.py:670-672` registers a stub `/health` route returning `{"status":"ok"}`. Starlette matches the first-registered route for a given path, so the real deep health check (`deep_health_check`, `main.py:865-871`) is registered later and is unreachable — it never runs, despite README claiming a deep DB/LLM/disk health check.

Fix:
- Remove the stub `/health` route at `main.py:670-672` entirely (just the route decorator + trivial handler — delete both).
- Confirm the `deep_health_check` route (`main.py:865-871`, also mounted at path `/health`) is now the only `/health` handler.
- Do not change `deep_health_check`'s logic or response shape — only remove the stub that shadows it.

Verify:
- `grep -n '"/health"' backend/main.py` shows exactly one route registration.
- Start the app (`.venv/bin/uvicorn backend.main:app` briefly, or via `TestClient` in a quick script/existing test) and confirm `GET /health` returns the deep-check payload shape (should include more than just `{"status": "ok"}` — check `deep_health_check`'s actual return fields first), not the old stub shape.
- Run any existing test that hits `/health` (grep `tests/` for `"/health"`) and confirm it still passes — if a test asserted the old stub's exact shape, that test was asserting the wrong (shadowed) behavior; update it to match the real deep-check response instead of preserving the stub's shape.

---

## Task 2: Fix RBAC bypass on Razorpay endpoints

Review finding §1.3: `/api/fetch-razorpay` and `/api/reconcile-razorpay` (`backend/main.py:983-1234`) authenticate via `verify_auth` (raw API-key match, `main.py:319-325`) only — they do not go through the RBAC permission system (`backend/rbac.py`) that `/upload` uses. Any caller with a valid API key, even one scoped to a VIEWER role, can trigger a live Razorpay fetch + reconciliation (a compute/write operation), while `/upload` correctly requires an `upload`-permission role for the equivalent action.

Fix:
- Look at how `/upload`'s handler enforces its permission check (find `require_upload` or equivalent dependency/decorator used on the `/upload` route in `main.py`, and its implementation in `backend/rbac.py`).
- Apply the identical `require_upload` permission gate to both `/api/fetch-razorpay` and `/api/reconcile-razorpay` handlers, using the same mechanism (FastAPI dependency, decorator, or inline check — whichever pattern `/upload` uses, for consistency).
- Do not invent a new permission tier — reuse `require_upload` exactly as instructed by the review's chosen fix (`require_upload`, decided when the plan was approved). Do not create a `require_reconcile` permission.

Verify:
- Existing RBAC tests (grep `tests/` for `rbac`, `require_upload`, `Role.VIEWER`) show the pattern for asserting a 403 on insufficient permission — write or extend a test asserting a VIEWER-role key gets `403` on both `/api/fetch-razorpay` and `/api/reconcile-razorpay`, and that an ADMIN or upload-permitted role key still succeeds (or reaches past the auth layer — downstream Razorpay-credentials-missing errors are fine, the point is it's not rejected at the RBAC layer).
- If `API.md` documents per-endpoint required roles/permissions, update the entries for these two endpoints to say they now require the same permission as `/upload`.
- Full `pytest -q` still green.

---

## Task 3: Fix silent ground-truth evaluation failure

Review finding §1.4: `backend/main.py:529-530` wraps the ground-truth JSON load + `evaluate_batch` call in a bare `except Exception: logger.warning(...)`. This means a missing `ground_truth.json` (expected/benign — evaluation is optional) and a genuine bug in `evaluate_batch` or malformed JSON (real failure) log identically, at the same level, with no way to tell them apart from logs.

Fix:
- Split the single broad `except Exception` at `main.py:529-530` into two:
  - `except FileNotFoundError:` — the expected case when no ground truth file is configured/present. Keep this quiet (e.g. `logger.info` or a debug-level log, or silent skip — match whatever the surrounding code's logging conventions are). No behavior change: evaluation is still skipped.
  - `except Exception:` — a real failure (malformed JSON, `evaluate_batch` raising). Use `logger.exception(...)` (not `logger.warning`) so the traceback is captured, and keep the message distinct from the FileNotFoundError case. Behavior otherwise unchanged (still non-fatal — the reconciliation flow continues either way, only the evaluation step is skipped).

Verify:
- Locate or add a test that (a) confirms the FileNotFoundError path still results in normal successful processing with no evaluation results (should already be covered — find it in `tests/`), and (b) exercises the "real failure" path — e.g. monkeypatch `evaluate_batch` to raise a `ValueError`, or point at a malformed ground-truth file, and assert the flow still completes (non-fatal) but a distinct log message/level fires. If no existing test infrastructure makes this easy, a targeted unit test around just this exception-handling block is sufficient — it does not need a full end-to-end reconciliation run.
- Full `pytest -q` still green.

---

## Task 4: Add logging to swallowed exceptions

Review finding §2 (Security): several broad exception handlers discard the exception with no diagnostic logging, at these locations:
- `backend/audit.py:247`
- `backend/audit.py:453`
- `backend/secret_manager.py:52`
- `backend/secret_manager.py:72`
- `backend/mcp_client.py:67`

(Note: `backend/database.py:34,45` was also flagged in the review but that whole file is deleted in Task 6 — skip it here.)

Fix:
- For each of the 5 locations above: read the surrounding function to understand what the `except` block currently does (swallow-and-continue, swallow-and-return-default, swallow-and-re-raise, etc.) and preserve that exact control-flow behavior — this is a logging-visibility fix only, not a behavior change.
- Add a `logger.exception("<short context message>")` call (or `logger.error(..., exc_info=True)` if that's the file's existing convention — check what pattern nearby logging calls in the same file already use and match it) immediately in the except block, before whatever the block already does. Write a context message specific to what failed (e.g. "Failed to read secret from environment" not a generic "error occurred").
- Confirm each file already has a `logger`/`logging` setup at module level (it should, based on the review's finding that logging already exists elsewhere in these files) — do not add new logging infrastructure, just use what's there.

Verify:
- `grep -n "except" backend/audit.py backend/secret_manager.py backend/mcp_client.py` — confirm the 5 target blocks each now have a `logger.exception` or equivalent call.
- Full `pytest -q` still green — this change must not alter any test's observed behavior (only log output changes, which tests generally don't assert on unless they specifically check log records; if any test does assert on log output for these blocks, it should still pass since we're adding logs, not changing return values/exceptions).

---

## Task 5: Document the no-auth-means-admin behavior

Review finding §2 (Security): `backend/rbac.py:76-77` — when `NIVARA_API_KEY` is unset, every request implicitly gets `Role.ADMIN`. This is intentional (demo-friendly default) but under-documented — current docs say something softer like "open access" without spelling out the actual risk.

Fix:
- In `README.md`, find where `NIVARA_API_KEY` is currently mentioned (search for it) and add/update a line stating plainly: if `NIVARA_API_KEY` is unset, every caller is treated as ADMIN — do not deploy without setting it.
- In `.env.example`, find the `NIVARA_API_KEY` line/section and add a comment above it making the same point concisely.
- Do not change any code in `rbac.py` — this task is documentation only, the behavior itself is correct as-is.

Verify:
- `grep -n "NIVARA_API_KEY" README.md .env.example` shows the new/updated wording is present and unambiguous about the ADMIN-by-default risk.
- No code changes, so no test impact — but run `pytest -q` anyway as a sanity check that nothing else in this working tree is broken.

---

## Task 6: Dead code removal

Review §3 identifies these as fully dead (zero references from any reachable code path) or effectively-dead (unreachable branch / hardcoded-always-same-value). Delete/fix each:

**Delete these files entirely:**
- `backend/database.py`
- `alembic/` — the whole directory (`alembic/env.py`, `alembic/versions/001_initial.py`, and the directory itself)
- `alembic.ini` (repo root)
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
- `backend/csv_schema.py` — **first** check `tests/test_integration.py` for its usage of `CSV_SCHEMAS` (grep `csv_schema` in `tests/`). If the test only imports constants for simple assertions, inline the needed values directly into the test file (or delete that specific test if it exists purely to test the dead module) before deleting `csv_schema.py`. If the test does something non-trivial with it that you're unsure how to handle, stop and report via NEEDS_CONTEXT rather than guessing.

**Remove a dead code branch (not a whole file):**
- `backend/engine.py:590-639` — the AI-invocation branch inside `run_engine`. Both call sites in `main.py` (around line 457 and line 1191 — line numbers may have shifted from Tasks 1-5, search for `run_engine(` calls) always pass `llm_client=None`, so this branch is unreachable via the API. Remove the dead branch, keep the early-return path that's actually exercised. If removing it makes the `llm_client` parameter to `run_engine` completely unused, simplify the function signature accordingly (but check test files for direct calls to `run_engine` with a non-None `llm_client` first — grep `tests/` — if any test does this, that test is exercising exactly this dead branch; per the review, either those tests should be removed alongside the branch, or if you find they test something still meaningful, report via NEEDS_CONTEXT before deciding).

**Remove a dead hardcoded field:**
- `backend/main.py:509` (line number may have shifted — search for `ai_auto = 0`) — remove the hardcoded `ai_auto` field and its exposure through `/status` and `/api/metrics` response payloads. Check `API.md` for documentation of this field and remove/update that too if present.

**Dependency cleanup:**
- Remove `psycopg2-binary` from `requirements.txt` — its only consumer was `database.py`, now deleted.
- Move `pytest` (and `pytest-cov` if listed) out of `requirements.txt` into a new `requirements-dev.txt` file, or into a `[project.optional-dependencies]` dev extra in `pyproject.toml` if that's a cleaner fit given the existing `pyproject.toml` structure — your call on which mechanism, but document the choice in the commit message and update `README.md`'s setup instructions if they reference installing dev dependencies.

Verify:
- `python3 -m compileall -q backend` clean (no import errors from the deletions).
- `grep -rn "csv_schema\|from backend.database\|from backend import database\|backend\.database\|ab_testing\|webhooks\|backend\.tasks\|cost_tracker\|semantic_validation" backend/ tests/ | grep -v ".pyc"` — should return nothing (or only comments/docs you intentionally left referencing the removal, which is fine).
- Full `pytest -q` green.
- `pip install -r requirements.txt` (or `uv pip install -r requirements.txt --python .venv/bin/python`) still resolves cleanly without `psycopg2-binary`.

---

## Task 7: `main.py` router split

Depends on Task 6 being complete (fewer files/branches to carry into the split).

Review §4: `backend/main.py` is ~1300+ lines mixing route handlers, an in-memory job store, a rate limiter, auth helpers, the Razorpay bridge logic, and pagination helpers in one file.
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
  main.py              # FastAPI() app creation, middleware, startup/shutdown (e.g. GROQ_API_KEY fail-fast check), router mounting only
  job_store.py         # in-memory job store + rate limiter, extracted as a dependency-free module (no route handlers, no imports from routes/)
  main.py              # app init, middleware, startup/shutdown, router mounting only
  job_store.py         # in-memory job store + rate limiter (extracted, no route handlers)
  routes/
    __init__.py
    upload.py           # POST /upload
    status.py           # GET /status/{job_id}
    audit.py             # GET /audit/{upload_hash}, GET /audit/{upload_hash}/verify, GET /settlement/{settlement_id}
    review.py            # POST /api/review/{settlement_id}/decision, GET /api/review/pending, GET /api/review/{settlement_id}
    audit.py             # GET /audit/{upload_hash}, /audit/{upload_hash}/verify, /settlement/{id}
    review.py            # POST /api/review/{id}/decision, GET /api/review/pending, GET /api/review/{id}
    razorpay.py          # POST /api/fetch-razorpay, POST /api/reconcile-razorpay
    metrics.py           # GET /api/metrics, GET /metrics
    health.py            # GET /health
```

Instructions:
- This is a structural move, not a rewrite — route handler bodies keep their existing logic (including the fixes already applied in Tasks 1-3) verbatim, just relocated into `APIRouter()`-based modules and mounted from `main.py`.
- Extract the in-memory job store and rate limiter into `job_store.py` as a module-level singleton (however it's currently implemented — likely a global dict/object in `main.py` today; find it and move it, keeping its interface the same). Any route file that needs it imports from `job_store.py`. Keep `job_store.py` free of imports from `routes/` to avoid circular imports.
- While moving `/upload`'s handler into `routes/upload.py` and `/api/reconcile-razorpay`'s handler into `routes/razorpay.py`: the review (§4, Code Quality) flagged that `/upload`'s response computes `unresolved` and `ai_investigations` fields that `/api/reconcile-razorpay`'s response omits — same underlying reconciliation flow, inconsistent response shape. While relocating both handlers, extract the shared response-shaping logic (whatever computes `unresolved`/`ai_investigations` from the job/results) into one function — put it in `job_store.py` or a new `backend/response_shaping.py`, whichever fits better once you see the actual code — and call it from both `routes/upload.py` and `routes/razorpay.py` so both endpoints return the same shape.
- In `routes/razorpay.py`, add validation that `from_date <= to_date` before calling the Razorpay fetch (return `400` on violation) — this parameter pair is currently unvalidated.
- `main.py` after the split should be short: app instantiation, middleware, startup checks, and `app.include_router(...)` calls for each router module. No route handler bodies should remain in `main.py`.

Verify:
- `python3 -m compileall -q backend` clean.
- Full `pytest -q` green. Tests likely import `from backend.main import app` (grep `tests/` to confirm) — that import path must keep working since `main.py` still creates and exposes `app`, just with routes mounted from elsewhere. If any test imports a route handler function directly from `backend.main` (rather than hitting it through the `TestClient`/`app`), update that import to the new module path.
- Manually verify (via existing integration tests, or a quick `TestClient` script if coverage is thin) that every endpoint listed in the target structure above still responds correctly — no 404s from a route that didn't get mounted.
- Confirm `/upload` and `/api/reconcile-razorpay` responses now include the same set of fields (`unresolved`, `ai_investigations` present in both) for equivalent underlying job state.

---

## Task 8: Coverage/mypy gate expansion

Depends on Task 7 being complete (coverage/mypy measured against the final file layout, not the pre-split one).

Review §5 + §4: `pyproject.toml` currently excludes 7 modules from both `[tool.coverage.run] omit` and `[tool.mypy] exclude`: `rbac.py`, `secret_manager.py`, `pii_redaction.py`, `webhooks.py`, `tasks.py`, `logging_config.py`, `semantic_validation.py`. Three of those (`webhooks.py`, `tasks.py`, `semantic_validation.py`) were deleted in Task 6, so only 4 remain to bring under the gate: `rbac.py`, `secret_manager.py`, `pii_redaction.py`, `logging_config.py`.

Fix (do these 4 modules one at a time, in this order — `rbac.py` and `pii_redaction.py` first since the review flags them as highest-risk):

For each module:
1. Remove it from the `omit` list in `[tool.coverage.run]` (`pyproject.toml`) and from the `exclude` list in `[tool.mypy]` (`pyproject.toml`).
2. Run `.venv/bin/python -m mypy backend/<module>.py` (mypy strict, per the existing `[tool.mypy]` config) and fix every reported error — add missing type annotations, eliminate `Any` leaks where a concrete type is knowable, add proper `Optional`/`None` narrowing. Do not change runtime behavior to satisfy mypy — only add/fix types and narrowing logic that doesn't alter what the code does.
3. Run `.venv/bin/python -m pytest --cov=backend --cov-report=term -q` and check this module's coverage percentage in the report. Write new unit tests in `tests/` (following the existing test file naming/structure convention — check what pattern other `tests/test_*.py` files use) to close gaps until the module clears the `fail_under = 85` threshold from `pyproject.toml`. For `rbac.py`: prioritize testing the permission-check branches, including the no-`NIVARA_API_KEY`-means-ADMIN path (Task 5 documented this behavior — Task 8 should verify it with a test). For `pii_redaction.py`: prioritize testing the actual redaction correctness (does it actually redact the PII fields it claims to) over just line coverage.
4. Commit each module's fix (types + tests) as part of this task — one task-level commit is fine, or up to 4 sub-commits if that's cleaner; your call, just report which you did.

Verify:
- `.venv/bin/python -m pytest --cov=backend --cov-report=term -q` passes with `fail_under = 85` and shows all 4 modules included in the coverage report (not omitted).
- `.venv/bin/python -m mypy backend --strict` (or check `pyproject.toml`'s actual mypy invocation convention/CI config for the exact command) passes with zero modules left in the `exclude` list.
- Full `pytest -q` still green (the full suite, not just the new tests).
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
