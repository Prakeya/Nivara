"""GET /status/{job_id} — processing status + results."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from backend.job_store import _jobs, _jobs_lock

router = APIRouter()


@router.get("/status/{job_id}")
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
