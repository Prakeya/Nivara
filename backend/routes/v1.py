"""API v1 router (P1-2.4): /v1/health, /v1/prompts, /v1/costs/{job_id},
/v1/jobs, /v1/jobs/{job_id}/results, /v1/audit/{upload_hash}.

Not called out explicitly in the Task 7 brief's target file structure, but
present in the pre-split main.py — kept as its own module (rather than
folded into health.py/status.py/audit.py) since it's a distinct versioned
sub-API with its own prefix and pagination helper, not one-to-one with any
single unversioned route file.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.job_store import _jobs, _jobs_lock
from backend.api_helpers import _get_audit_logger

router = APIRouter(prefix="/v1")


@router.get("/health")
async def v1_health() -> dict[str, Any]:
    from backend.health import deep_health_check
    return deep_health_check()


@router.get("/prompts")
async def v1_list_prompts() -> dict[str, Any]:
    from backend.prompt_registry import get_registry
    return {"prompts": get_registry().list_prompts()}


@router.get("/costs/{job_id}")
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

@router.get("/jobs")
async def v1_list_jobs(page: int = 1, page_size: int = 50) -> dict[str, Any]:
    """List all jobs with pagination."""
    with _jobs_lock:
        jobs = [
            {"job_id": j.job_id, "status": j.status, "created_at": j.created_at}
            for j in _jobs.values()
        ]
    jobs.sort(key=lambda x: x["created_at"], reverse=True)
    return paginate(jobs, page, page_size)


@router.get("/jobs/{job_id}/results")
async def v1_get_job_results(job_id: str, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    """Get paginated results for a job."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return paginate(job.results, page, page_size)


@router.get("/audit/{upload_hash}")
async def v1_get_audit(upload_hash: str, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    """Get paginated audit records."""
    audit = _get_audit_logger()
    try:
        records = audit.get_batch(upload_hash)
        items = [r.to_dict() for r in records]
    finally:
        audit.close()
    return paginate(items, page, page_size)
