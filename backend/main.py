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
import uuid
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from backend.audit import AuditLogger
from backend.batch_analyzer import analyze_batch
from backend.engine import run_engine
from backend.ingestion import IngestionResult, ingest_csvs
from backend.models import HumanReviewDecision, ResolutionStatus

logger = logging.getLogger("nivara.api")

app = FastAPI(
    title="Nivara",
    description="AI Settlement Intelligence Agent",
    version="0.1.0",
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
    results: list[dict] = field(default_factory=list)
    batch_analysis: dict = field(default_factory=dict)
    audit_records: list[dict] = field(default_factory=list)


_jobs: dict[str, JobResult] = {}

# ---------------------------------------------------------------------------
# Rate limiting (simple in-memory sliding window)
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Per-IP sliding window rate limiter."""

    def __init__(self):
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
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

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


def _get_llm_client():
    """Return the production LLM client based on environment configuration.

    - If OPENAI_API_KEY is set → OpenAIClient (real LLM)
    - If OPENAI_API_KEY is missing → DemoLLMClient (heuristic, clearly labeled MOCK)
    - Never crashes the application.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        try:
            from backend.ai_investigator import OpenAIClient
            return OpenAIClient(api_key=api_key)
        except Exception:
            pass
    # Fallback: deterministic heuristic client for demo mode
    from backend.ai_investigator import DemoLLMClient
    return DemoLLMClient()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_hash(file_paths: list[str]) -> str:
    """Compute SHA-256 hash using canonical ingestion hash (sort CSVs by first column)."""
    from backend.ingestion import compute_upload_hash
    return compute_upload_hash(file_paths)


def _result_to_dict(r) -> dict:
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
# POST /upload
# ---------------------------------------------------------------------------

@app.post("/upload")
async def upload_files(
    request: Request,
    transactions: UploadFile = File(...),
    settlements: UploadFile = File(...),
    refunds: UploadFile = File(...),
    bank_credits: UploadFile = File(...),
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
        results = await asyncio.to_thread(
            run_engine,
            transactions=ingestion.transactions,
            settlements=ingestion.settlements,
            refunds=ingestion.refunds,
            bank_credits=ingestion.bank_credits,
            llm_client=llm_client,
        )

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
        gt_path = os.path.join("data", "evaluation", "ground_truth.json")
        if not os.path.exists(gt_path):
            gt_path = os.path.join("data", "demo", "ground_truth.json")
        if os.path.exists(gt_path):
            try:
                import json as _json
                with open(gt_path) as _f:
                    gt_data = _json.load(_f)
                gt_list = gt_data if isinstance(gt_data, list) else gt_data.get("ground_truth", [])
                if len(gt_list) == len(results):
                    from backend.evaluation import evaluate_batch
                    batch_start = time.time()
                    metrics = evaluate_batch(results, gt_list, batch_time_seconds=0.0, ai_client_available=llm_client is not None)
                    match_rate = metrics.match_rate
            except Exception:
                pass  # Ground truth not available or mismatched — skip

        job.status = "completed"
        job.total_settlements = len(results)
        job.clean_matches = clean
        job.exceptions = exceptions
        job.unresolved = unresolved
        job.math_discrepancies = math_disc
        job.ai_investigations = ai_inv
        job.ai_auto_approved = ai_auto
        job.match_rate = match_rate
        job.results = [_result_to_dict(r) for r in results]
        job.batch_analysis = batch_analysis
        job.audit_records = audit_records

    except HTTPException:
        raise  # Let HTTP exceptions propagate as-is
    except Exception as exc:
        logger.exception("Upload processing failed for job %s", job_id)
        job = _jobs.get(job_id)
        if job:
            job.status = "error"
            job.error = "An internal error occurred during processing. Check server logs for details."
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

    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    content = {
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
    decision: str,
    reason: str,
    reviewer_id: str = "anonymous",
) -> JSONResponse:
    """Submit a human review decision for a settlement.

    Decision must be one of: APPROVE, REJECT, MODIFY.
    Updates resolution_status to RESOLVED_BY_HUMAN or REJECTED.
    """
    # Rate limit check
    client_ip = _get_client_ip(request)
    if not _rate_limiter.check_api(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")

    # Validate parameter lengths
    if len(reason) > MAX_REVIEW_REASON_LENGTH:
        raise HTTPException(status_code=400, detail=f"Reason exceeds maximum length of {MAX_REVIEW_REASON_LENGTH} characters.")
    if len(reviewer_id) > MAX_REVIEWER_ID_LENGTH:
        raise HTTPException(status_code=400, detail=f"Reviewer ID exceeds maximum length of {MAX_REVIEWER_ID_LENGTH} characters.")

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

app.mount("/static", StaticFiles(directory=str(_frontend_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_path = _frontend_dir / "index.html"
    return HTMLResponse(content=index_path.read_text())
