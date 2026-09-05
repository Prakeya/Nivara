"""GET /api/metrics, GET /metrics."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.job_store import _jobs, _jobs_lock
from backend.rbac import require_configure

router = APIRouter()


@router.get("/metrics")
async def metrics() -> JSONResponse:
    """Prometheus metrics endpoint."""
    from backend.metrics import get_metrics, get_content_type
    return JSONResponse(
        content=get_metrics().decode(),
        media_type=get_content_type(),
    )


@router.get("/api/metrics")
async def api_metrics(_auth: None = Depends(require_configure)) -> JSONResponse:
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
            "llm": llm,
            "groq_free_tier": groq_quota,
            # Groq free tier is $0; kept explicit so the dashboard shows the real cost.
            "estimated_cost_inr": 0.0,
        },
    )
