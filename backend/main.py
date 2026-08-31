"""
Phase 10: FastAPI Endpoints

Endpoints:
- POST /upload — Accept 4 CSVs, return job_id
- GET /status/{job_id} — Processing status + results
- GET /audit/{upload_hash} — Audit trail for a batch
- GET /settlement/{settlement_id} — Settlement audit history
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import tempfile
import threading
import uuid
import time
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from backend.audit import AuditLogger
from backend.batch_analyzer import analyze_batch
from backend.engine import run_engine
from backend.ingestion import IngestionResult, ingest_csvs
from backend.models import HumanReviewDecision, ReconciliationResult, ResolutionStatus, ReviewDecision

logger = logging.getLogger("nivara.api")

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    """Fail fast at boot if GROQ_API_KEY is missing (WATCH-G4)."""
    try:
        _validate_llm_config()
    except RuntimeError as exc:
        logger.error("Startup LLM validation failed: %s", exc)
        raise
    logger.info("Groq configuration validated")
    yield


app = FastAPI(
    title="Nivara",
    description="AI Settlement Intelligence Agent",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
CSV_MAGIC_BYTES = [b"application/csv", b"text/csv", b"application/vnd.ms-excel"]
MAX_REVIEW_REASON_LENGTH = 2000
MAX_REVIEWER_ID_LENGTH = 100
MAX_JOBS = 10000


# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------

@dataclass
class JobResult:
    job_id: str
    status: str  # "processing" | "completed" | "error"
    upload_hash: str = ""
    created_at: str = ""
    error: str = ""
    # Populated after processing
    total_settlements: int = 0
    clean_matches: int = 0
    exceptions: int = 0
    unresolved: int = 0
    math_discrepancies: int = 0
    ai_investigations: int = 0
    ai_auto_approved: int = 0
    match_rate: float = 0.0
    csv_counts: dict[str, Any] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)
    batch_analysis: list[dict[str, Any]] = field(default_factory=list)
    audit_records: list[dict[str, Any]] = field(default_factory=list)


_jobs: dict[str, JobResult] = {}
_jobs_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Rate limiting (simple in-memory sliding window)
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Per-IP sliding window rate limiter."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._upload_limit = 100  # req per 60s
        self._api_limit = 300  # req per 60s
        self._window = 60.0

    def _check(self, key: str, limit: int) -> bool:
        now = time.time()
        cutoff = now - self._window
        self._hits[key] = [t for t in self._hits[key] if t > cutoff]
        if len(self._hits[key]) >= limit:
            return False
        self._hits[key].append(now)
        return True

    def check_upload(self, ip: str) -> bool:
        return self._check(f"upload:{ip}", self._upload_limit)

    def check_api(self, ip: str) -> bool:
        return self._check(f"api:{ip}", self._api_limit)

_rate_limiter = _RateLimiter()


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response  # type: ignore[no-any-return]

app.add_middleware(SecurityHeadersMiddleware)


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Audit DB path (persistent, survives restart)
# ---------------------------------------------------------------------------

_AUDIT_DB_DIR = Path("data/audit")
_AUDIT_DB_PATH = _AUDIT_DB_DIR / "audit.db"


def _get_audit_logger() -> AuditLogger:
    """Return an AuditLogger backed by the persistent SQLite DB."""
    _AUDIT_DB_DIR.mkdir(parents=True, exist_ok=True)
    return AuditLogger(str(_AUDIT_DB_PATH))


def _get_llm_client() -> Optional[str]:
    """Return a truthy value if Groq is available.

    The architecture uses a Groq-first fallback chain (70B → 8B → UNRESOLVED).
    Returns "configured" when GROQ_API_KEY is set, which signals to run_engine
    that AI investigation should be enabled.
    """
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        return "configured"
    return None


def _validate_llm_config() -> None:
    """
    Fail-fast GROQ_API_KEY validation at startup (WATCH-G4).

    Raises:
        RuntimeError: when GROQ_API_KEY is missing/empty or the Groq SDK is
        unavailable. The deterministic engine still works, but AI investigation
        (MATH_DISCREPANCY) would be permanently UNRESOLVED — surface loudly at
        boot instead. Key validity is confirmed on the first real API call.
    """
    from backend.groq_client import GroqClient

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not groq_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Nivara requires a Groq API key for AI "
            "investigation. Set GROQ_API_KEY in the environment or a .env file."
        )
    # Instantiation fails fast if the groq SDK is not installed.
    GroqClient(api_key=groq_key)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_hash(file_paths: list[str]) -> str:
    """Compute SHA-256 hash using canonical ingestion hash (sort CSVs by first column)."""
    from backend.ingestion import compute_upload_hash
    return compute_upload_hash(file_paths)


def _result_to_dict(r: ReconciliationResult, gt_label: str | None = None) -> dict[str, Any]:
    """Convert ReconciliationResult to JSON-safe dict."""
    d = {
        "settlement_id": r.settlement_id,
        "decision_state": r.decision.value if hasattr(r.decision, "value") else str(r.decision),
        "difference_paise": r.difference_paise,
        "expected_amount_paise": r.expected_amount_paise,
        "actual_amount_paise": r.actual_amount_paise,
        "deterministic_checks_passed": r.deterministic_checks_passed,
        "deterministic_checks_failed": r.deterministic_checks_failed,
        "escalate_to_human": r.escalate_to_human,
        "ai_mode": getattr(r, "ai_mode", None),
    }
    if gt_label is not None:
        d["gt_label"] = gt_label
    if r.ai_response is not None:
        d["ai_response"] = {
            "classification": r.ai_response.classification.value
            if hasattr(r.ai_response.classification, "value")
            else str(r.ai_response.classification),
            "explanation": r.ai_response.explanation,
            "raw_confidence": r.ai_response.raw_confidence,
            "cited_evidence": r.ai_response.cited_evidence,
            "recommended_action": r.ai_response.recommended_action.value
            if hasattr(r.ai_response.recommended_action, "value")
            else str(r.ai_response.recommended_action),
        }
    return d


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

_NIVARA_API_KEY = os.environ.get("NIVARA_API_KEY", "")

async def verify_auth(request: Request) -> None:
    """Verify API key via X-API-Key header. Skip if NIVARA_API_KEY is not set."""
    if not _NIVARA_API_KEY:
        return  # No auth configured — open access
    api_key = request.headers.get("X-API-Key", "")
    if api_key != _NIVARA_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# POST /upload
# ---------------------------------------------------------------------------

@app.post("/upload")
async def upload_files(
    request: Request,
    transactions: UploadFile = File(...),
    settlements: UploadFile = File(...),
    refunds: UploadFile = File(...),
    bank_credits: UploadFile = File(...),
    _auth: None = Depends(verify_auth),
) -> JSONResponse:
    """Accept 4 CSV files, process reconciliation, return job_id."""
    # Rate limit check
    client_ip = _get_client_ip(request)
    if not _rate_limiter.check_upload(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")

    job_id = str(uuid.uuid4())
    from datetime import timezone
    created_at = datetime.now(timezone.utc).isoformat()

    # Validate content types and file sizes before processing
    upload_files_list = [
        (transactions, "transactions.csv"),
        (settlements, "settlements.csv"),
        (refunds, "refunds.csv"),
        (bank_credits, "bank_credits.csv"),
    ]

    # Check file sizes and content types
    for upload_file, name in upload_files_list:
        # Validate content type header
        ct = (upload_file.content_type or "").lower().strip()
        if ct and ct not in ("text/csv", "application/csv", "application/vnd.ms-excel", "application/octet-stream", ""):
            raise HTTPException(
                status_code=415,
                detail=f"File '{name}' has unsupported content type: {ct}. Expected CSV.",
            )

    # Save uploaded files to temp directory (cleaned up in finally block)
    tmp_dir = tempfile.mkdtemp()
    file_paths = []
    try:
        for upload_file, name in upload_files_list:
            fp = Path(tmp_dir) / name
            content = await upload_file.read()

            # Validate file size (50MB limit)
            if len(content) > MAX_UPLOAD_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"File '{name}' exceeds maximum size of {MAX_UPLOAD_SIZE // (1024*1024)}MB.",
                )

            # Validate content is text-based (not binary) by checking for null bytes
            if b"\x00" in content[:8192]:
                raise HTTPException(
                    status_code=415,
                    detail=f"File '{name}' appears to be binary, not a CSV text file.",
                )

            fp.write_bytes(content)
            file_paths.append(str(fp))

        upload_hash = _compute_hash(file_paths)

        # Job eviction: cap at MAX_JOBS, evict oldest
        with _jobs_lock:
            if len(_jobs) >= MAX_JOBS:
                oldest_ids = sorted(_jobs.keys(), key=lambda k: _jobs[k].created_at)[:len(_jobs) // 4]
                for old_id in oldest_ids:
                    _jobs.pop(old_id, None)

            job = JobResult(
                job_id=job_id,
                status="processing",
                upload_hash=upload_hash,
                created_at=created_at,
            )
            _jobs[job_id] = job

        # Ingest (sync CSV parsing — offload to threadpool)
        ingestion: IngestionResult = await asyncio.to_thread(
            ingest_csvs,
            transactions_path=file_paths[0],
            settlements_path=file_paths[1],
            refunds_path=file_paths[2],
            bank_credits_path=file_paths[3],
        )

        # Run engine with real LLM client (or None if no API key)
        llm_client = _get_llm_client()
        if llm_client is not None:
            n_settlements = len(ingestion.settlements)
            if n_settlements:
                from backend.groq_client import check_batch_feasible
                try:
                    check_batch_feasible(n_settlements)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Batch rejected under Groq free-tier budget: {exc}",
                    ) from exc
        results = await asyncio.to_thread(
            run_engine,
            transactions=ingestion.transactions,
            settlements=ingestion.settlements,
            refunds=ingestion.refunds,
            bank_credits=ingestion.bank_credits,
            llm_client=llm_client,
        )

        # Store CSV row counts for frontend display
        csv_counts = {
            "transactions": len(ingestion.transactions),
            "settlements": len(ingestion.settlements),
            "refunds": len(ingestion.refunds),
            "bank_credits": len(ingestion.bank_credits),
        }

        # Batch analysis
        patterns = await asyncio.to_thread(analyze_batch, results)
        batch_analysis = [
            {
                "pattern_type": p.pattern_type,
                "affected_settlement_ids": p.affected_settlement_ids,
                "confidence": p.confidence,
                "recommended_action": p.recommended_action,
                "description": p.description,
            }
            for p in patterns
        ]

        # Audit log (persistent storage)
        audit = _get_audit_logger()
        audit.log_batch(upload_hash, results)
        audit_records = [r.to_dict() for r in audit.get_batch(upload_hash)]

        # Counts — REVIEW_REQUIRED counts as exception (human-reviewable),
        # matching the evaluation's definition of "correctly caught".
        from backend.models import DecisionState
        clean = sum(1 for r in results if r.decision == DecisionState.CLEAN_MATCH)
        math_disc = sum(1 for r in results if r.decision == DecisionState.MATH_DISCREPANCY)
        exceptions = sum(
            1 for r in results
            if r.decision in (
                DecisionState.DETERMINISTIC_EXCEPTION,
                DecisionState.REVIEW_REQUIRED,
            )
        )
        unresolved = sum(
            1 for r in results
            if r.decision in (
                DecisionState.UNPROCESSED,
                DecisionState.UNRESOLVED,
            )
        )
        ai_inv = sum(1 for r in results if r.ai_response is not None)
        ai_auto = 0  # AI never auto-approves (enforced by schema)

        # Compute match rate against ground truth if available
        match_rate = 0.0
        gt_map = {}
        gt_path = os.path.join("data", "evaluation", "ground_truth.json")
        if not os.path.exists(gt_path):
            gt_path = os.path.join("data", "demo", "ground_truth.json")
        if os.path.exists(gt_path):
            try:
                import json as _json
                with open(gt_path) as _f:
                    gt_data = _json.load(_f)
                gt_list = gt_data if isinstance(gt_data, list) else gt_data.get("ground_truth", [])
                gt_map = {item["settlement_id"]: item.get("label") for item in gt_list if "settlement_id" in item}
                if len(gt_list) == len(results):
                    from backend.evaluation import evaluate_batch
                    batch_start = time.time()
                    metrics = evaluate_batch(results, gt_list, batch_time_seconds=0.0, ai_client_available=llm_client is not None)
                    match_rate = metrics.match_rate
            except Exception as exc:
                logger.warning("Ground truth evaluation failed: %s", exc)  # Ground truth not available or mismatched — skip

        job.status = "completed"
        job.total_settlements = len(results)
        job.clean_matches = clean
        job.exceptions = exceptions
        job.unresolved = unresolved
        job.math_discrepancies = math_disc
        job.ai_investigations = ai_inv
        job.ai_auto_approved = ai_auto
        job.match_rate = match_rate
        job.csv_counts = csv_counts
        job.results = [_result_to_dict(r, gt_label=gt_map.get(r.settlement_id)) for r in results]
        job.batch_analysis = batch_analysis
        job.audit_records = audit_records

    except HTTPException:
        raise  # Let HTTP exceptions propagate as-is
    except Exception as exc:
        logger.exception("Upload processing failed for job %s", job_id)
        with _jobs_lock:
            failing_job = _jobs.get(job_id)
        if failing_job is not None:
            failing_job.status = "error"
            failing_job.error = "An internal error occurred during processing. Check server logs for details."
    finally:
        # Clean up temp directory
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "upload_hash": upload_hash, "status": job.status},
    )


# ---------------------------------------------------------------------------
# GET /status/{job_id}
# ---------------------------------------------------------------------------

@app.get("/status/{job_id}")
async def get_status(job_id: str) -> JSONResponse:
    """Return processing status and results for a job."""
    # Validate job_id format (UUID)
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid job ID format: {job_id}")

    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    content: dict[str, Any] = {
        "job_id": job.job_id,
        "status": job.status,
        "upload_hash": job.upload_hash,
        "created_at": job.created_at,
    }

    if job.status == "error":
        content["error"] = job.error  # Already sanitized in upload handler
    elif job.status == "completed":
        content["total_settlements"] = job.total_settlements
        content["clean_matches"] = job.clean_matches
        content["exceptions"] = job.exceptions
        content["unresolved"] = job.unresolved
        content["math_discrepancies"] = job.math_discrepancies
        content["ai_investigations"] = job.ai_investigations
        content["ai_auto_approved"] = job.ai_auto_approved
        content["match_rate"] = job.match_rate
        content["results"] = job.results
        content["batch_analysis"] = job.batch_analysis
        content["audit_records"] = job.audit_records
        # Compute blind spots from ground truth labels
        blind_spots = sum(
            1 for r in job.results
            if r.get("gt_label") in ("refund_after_settlement", "timing_race")
        )
        content["blind_spots"] = blind_spots
        # CSV row counts from file ingestion
        content["csv_counts"] = job.csv_counts
        # Determine ai_mode from results
        ai_modes = {r.get("ai_mode") for r in job.results if r.get("ai_mode")}
        content["ai_mode"] = "demo" if "demo" in ai_modes else ("live" if "live" in ai_modes else None)

    return JSONResponse(content=content)


# ---------------------------------------------------------------------------
# GET /audit/{upload_hash}
# ---------------------------------------------------------------------------

@app.get("/audit/{upload_hash}")
async def get_audit(upload_hash: str) -> JSONResponse:
    """Return all audit records for a given upload hash.
    Reads directly from the persistent SQLite database."""
    # Validate upload_hash format (64-char hex SHA-256)
    if not re.fullmatch(r"[0-9a-f]{64}", upload_hash):
        raise HTTPException(status_code=400, detail=f"Invalid upload hash format: {upload_hash}")

    audit = _get_audit_logger()
    records = audit.get_batch(upload_hash)
    if not records:
        raise HTTPException(status_code=404, detail=f"Audit not found for hash {upload_hash}")

    return JSONResponse(content={
        "upload_hash": upload_hash,
        "total_records": len(records),
        "records": [r.to_dict() for r in records],
    })


# ---------------------------------------------------------------------------
# GET /settlement/{settlement_id}
# ---------------------------------------------------------------------------

@app.get("/settlement/{settlement_id}")
async def get_settlement(settlement_id: str) -> JSONResponse:
    """Return audit history for a settlement across all batches.
    Reads directly from the persistent SQLite database."""
    # Validate settlement_id format (SETL_NNNN pattern)
    if not re.fullmatch(r"SETL_\d{4,}", settlement_id):
        raise HTTPException(status_code=400, detail=f"Invalid settlement ID format: {settlement_id}")

    audit = _get_audit_logger()
    history = audit.get_settlement_history(settlement_id)

    return JSONResponse(content={
        "settlement_id": settlement_id,
        "total_records": len(history),
        "records": [r.payload() for r in history],
    })


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(content={"status": "ok", "version": "0.1.0"})


@app.get("/audit/{upload_hash}/verify")
async def verify_audit_chain(upload_hash: str) -> JSONResponse:
    """Verify hash chain integrity for a batch. Returns verification result."""
    if not re.fullmatch(r"[0-9a-f]{64}", upload_hash):
        raise HTTPException(status_code=400, detail=f"Invalid upload hash format: {upload_hash}")
    audit = _get_audit_logger()
    result = audit.verify_chain(upload_hash)
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# Human Review API
# ---------------------------------------------------------------------------

# In-memory store for human review decisions (keyed by settlement_id)
_human_reviews: dict[str, HumanReviewDecision] = {}


@app.post("/api/review/{settlement_id}/decision")
async def submit_human_review(
    request: Request,
    settlement_id: str,
    body: ReviewDecision,
    _auth: None = Depends(verify_auth),
) -> JSONResponse:
    """Submit a human review decision for a settlement.

    Decision must be one of: APPROVE, REJECT, MODIFY.
    Updates resolution_status to RESOLVED_BY_HUMAN or REJECTED.
    """
    # Rate limit check
    client_ip = _get_client_ip(request)
    if not _rate_limiter.check_api(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")

    decision = body.decision
    reason = body.reason
    reviewer_id = body.reviewer_id

    valid_decisions = {"APPROVE", "REJECT", "MODIFY"}
    if decision.upper() not in valid_decisions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid decision '{decision}'. Must be one of: {valid_decisions}",
        )

    review = HumanReviewDecision(
        settlement_id=settlement_id,
        decision=decision.upper(),
        reason=reason,
        reviewer_id=reviewer_id,
    )
    _human_reviews[settlement_id] = review

    # Update resolution_status in job results
    for job in _jobs.values():
        for result_dict in job.results:
            if result_dict.get("settlement_id") == settlement_id:
                if decision.upper() == "REJECT":
                    result_dict["resolution_status"] = "REJECTED"
                else:
                    result_dict["resolution_status"] = "RESOLVED_BY_HUMAN"
                result_dict["human_review"] = {
                    "decision": review.decision,
                    "reason": review.reason,
                    "reviewer_id": review.reviewer_id,
                    "timestamp": review.timestamp.isoformat(),
                }

    # Log to audit trail
    audit = _get_audit_logger()
    audit.log_human_review(settlement_id, review)

    return JSONResponse(content={
        "settlement_id": settlement_id,
        "decision": review.decision,
        "reason": review.reason,
        "reviewer_id": review.reviewer_id,
        "timestamp": review.timestamp.isoformat(),
        "status": "accepted",
    })


@app.get("/api/review/pending")
async def get_pending_reviews(request: Request) -> JSONResponse:
    """List all settlements pending human review across all jobs."""
    client_ip = _get_client_ip(request)
    if not _rate_limiter.check_api(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    pending = []
    for job in _jobs.values():
        if job.status != "completed":
            continue
        for result_dict in job.results:
            if result_dict.get("escalate_to_human") and result_dict.get("decision_state") in (
                "REVIEW_REQUIRED", "UNRESOLVED", "MATH_DISCREPANCY",
            ):
                settlement_id = result_dict.get("settlement_id")
                if settlement_id not in _human_reviews:
                    pending.append({
                        "settlement_id": settlement_id,
                        "decision_state": result_dict.get("decision_state"),
                        "difference_paise": result_dict.get("difference_paise"),
                        "ai_classification": (
                            result_dict.get("ai_response", {}).get("classification")
                            if result_dict.get("ai_response") else None
                        ),
                        "ai_explanation": (
                            result_dict.get("ai_response", {}).get("explanation")
                            if result_dict.get("ai_response") else None
                        ),
                        "ai_confidence": (
                            result_dict.get("ai_response", {}).get("raw_confidence")
                            if result_dict.get("ai_response") else None
                        ),
                        "job_id": job.job_id,
                    })

    return JSONResponse(content={
        "total_pending": len(pending),
        "settlements": pending,
    })


@app.get("/api/review/{settlement_id}")
async def get_review_status(request: Request, settlement_id: str) -> JSONResponse:
    """Get the review status and audit trail for a specific settlement."""
    client_ip = _get_client_ip(request)
    if not _rate_limiter.check_api(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    review = _human_reviews.get(settlement_id)

    # Look up in job results for full context
    result_info = None
    for job in _jobs.values():
        for result_dict in job.results:
            if result_dict.get("settlement_id") == settlement_id:
                result_info = result_dict
                break
        if result_info:
            break

    content = {
        "settlement_id": settlement_id,
        "reviewed": review is not None,
    }

    if review:
        content["review"] = {
            "decision": review.decision,
            "reason": review.reason,
            "reviewer_id": review.reviewer_id,
            "timestamp": review.timestamp.isoformat(),
        }

    if result_info:
        content["result"] = result_info
        content["resolution_status"] = result_info.get("resolution_status", "OPEN")

    return JSONResponse(content=content)


# ---------------------------------------------------------------------------
# Static frontend serving
# ---------------------------------------------------------------------------

_frontend_dir = Path(__file__).parent.parent / "frontend"
_dist_dir = _frontend_dir / "dist"
_assets_dir = _dist_dir / "assets"

# Prefer the Vite production build (frontend/dist) when present; fall back to
# the legacy dev layout (frontend/index.html + /static) otherwise.
_LIVE_FRONTEND = _assets_dir.is_dir()

if _LIVE_FRONTEND:
    app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")
else:
    app.mount("/static", StaticFiles(directory=str(_frontend_dir)), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def serve_index() -> HTMLResponse:
    index_path = (_dist_dir if _LIVE_FRONTEND else _frontend_dir) / "index.html"
    return HTMLResponse(content=index_path.read_text())


# ---------------------------------------------------------------------------
# Health check (P1-2.7)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check() -> JSONResponse:
    """Deep health check: DB, LLM, disk."""
    from backend.health import deep_health_check
    result = deep_health_check()
    status_code = 200 if result["status"] == "healthy" else 503
    return JSONResponse(content=result, status_code=status_code)


# ---------------------------------------------------------------------------
# Prometheus metrics (P1-2.6)
# ---------------------------------------------------------------------------

@app.get("/metrics")
async def metrics() -> JSONResponse:
    """Prometheus metrics endpoint."""
    from backend.metrics import get_metrics, get_content_type
    return JSONResponse(
        content=get_metrics().decode(),
        media_type=get_content_type(),
    )


@app.get("/api/metrics")
async def api_metrics() -> JSONResponse:
    """JSON metrics for the Metrics Dashboard (pie chart, quota, latency, cost)."""
    from datetime import datetime as _dt, timezone as _tz
    from backend.metrics import (
        llm_metrics_snapshot,
        groq_daily_usage_snapshot,
    )

    with _jobs_lock:
        jobs = list(_jobs.values())

    total_jobs = len(jobs)
    completed = [j for j in jobs if j.status == "completed"]
    errored = [j for j in jobs if j.status == "error"]

    settlements_processed = sum(j.total_settlements for j in completed)
    decision_breakdown = {
        "clean": sum(j.clean_matches for j in completed),
        "exceptions": sum(j.exceptions for j in completed),
        "math_discrepancy": sum(j.math_discrepancies for j in completed),
        "unresolved": sum(j.unresolved for j in completed),
    }
    ai_investigations = sum(j.ai_investigations for j in completed)
    ai_auto_approved = sum(j.ai_auto_approved for j in completed)

    match_rates = [j.match_rate for j in completed if j.match_rate and j.match_rate > 0]
    avg_match_rate = round(sum(match_rates) / len(match_rates), 4) if match_rates else 0.0

    llm = llm_metrics_snapshot()
    groq_quota = groq_daily_usage_snapshot()

    return JSONResponse(
        content={
            "generated_at": _dt.now(_tz.utc).isoformat(),
            "active_ai": bool(os.environ.get("GROQ_API_KEY")),
            "batches_processed": len(completed),
            "settlements_processed": settlements_processed,
            "jobs_failed": len(errored),
            "error_rate": round(len(errored) / total_jobs, 4) if total_jobs else 0.0,
            "avg_match_rate": avg_match_rate,
            "decision_breakdown": decision_breakdown,
            "ai_investigations_total": ai_investigations,
            "ai_auto_approved_total": ai_auto_approved,
            "llm": llm,
            "groq_free_tier": groq_quota,
            # Groq free tier is $0; kept explicit so the dashboard shows the real cost.
            "estimated_cost_inr": 0.0,
        },
    )


# ---------------------------------------------------------------------------
# API v1 router (P1-2.4)
# ---------------------------------------------------------------------------

from fastapi import APIRouter

v1_router = APIRouter(prefix="/v1")


@v1_router.get("/health")
async def v1_health() -> dict[str, Any]:
    from backend.health import deep_health_check
    return deep_health_check()


@v1_router.get("/prompts")
async def v1_list_prompts() -> dict[str, Any]:
    from backend.prompt_registry import get_registry
    return {"prompts": get_registry().list_prompts()}


@v1_router.get("/costs/{job_id}")
async def v1_get_costs(job_id: str) -> dict[str, Any]:
    """Get cost breakdown for a job."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    # Cost data is tracked during processing — return summary from batch_analysis
    return {
        "job_id": job_id,
        "total_settlements": job.total_settlements,
        "match_rate": job.match_rate,
    }


app.include_router(v1_router)


# ---------------------------------------------------------------------------
# Pagination helper (P1-2.3)
# ---------------------------------------------------------------------------

def paginate(items: list[Any], page: int = 1, page_size: int = 50) -> dict[str, Any]:
    """Paginate a list of items."""
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "items": items[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


# ---------------------------------------------------------------------------
# Versioned endpoints with pagination (P1-2.3 + P1-2.4)
# ---------------------------------------------------------------------------

@v1_router.get("/jobs")
async def v1_list_jobs(page: int = 1, page_size: int = 50) -> dict[str, Any]:
    """List all jobs with pagination."""
    with _jobs_lock:
        jobs = [
            {"job_id": j.job_id, "status": j.status, "created_at": j.created_at}
            for j in _jobs.values()
        ]
    jobs.sort(key=lambda x: x["created_at"], reverse=True)
    return paginate(jobs, page, page_size)


@v1_router.get("/jobs/{job_id}/results")
async def v1_get_job_results(job_id: str, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    """Get paginated results for a job."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return paginate(job.results, page, page_size)


@v1_router.get("/audit/{upload_hash}")
async def v1_get_audit(upload_hash: str, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    """Get paginated audit records."""
    audit = _get_audit_logger()
    try:
        records = audit.get_batch(upload_hash)
        items = [r.to_dict() for r in records]
    finally:
        audit.close()
    return paginate(items, page, page_size)


# ---------------------------------------------------------------------------
# SPA fallback: serve the frontend index for unknown non-API routes
# ---------------------------------------------------------------------------

_API_PREFIXES = (
    "api/",
    "upload/",
    "status/",
    "audit/",
    "v1/",
    "health",
    "metrics/",
    "static/",
    "assets/",
    "docs",
    "redoc",
    "openapi.json",
)


@app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
def serve_spa_fallback(full_path: str) -> HTMLResponse:
    if full_path.startswith(_API_PREFIXES):
        raise HTTPException(status_code=404, detail="Resource not found")
    index_path = (_dist_dir if _LIVE_FRONTEND else _frontend_dir) / "index.html"
    return HTMLResponse(content=index_path.read_text())
