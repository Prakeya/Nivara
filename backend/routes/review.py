"""POST /api/review/{settlement_id}/decision, GET /api/review/pending, GET /api/review/{settlement_id}."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.api_helpers import _get_audit_logger, _get_client_ip
from backend.job_store import _human_reviews, _jobs, _rate_limiter
from backend.models import HumanReviewDecision, ReviewDecision
from backend.rbac import require_read, require_review

router = APIRouter()

MAX_REVIEW_REASON_LENGTH = 2000
MAX_REVIEWER_ID_LENGTH = 100


@router.post("/api/review/{settlement_id}/decision")
async def submit_human_review(
    request: Request,
    settlement_id: str,
    body: ReviewDecision,
    _auth: None = Depends(require_review),
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


@router.get("/api/review/pending")
async def get_pending_reviews(request: Request, _auth: None = Depends(require_read)) -> JSONResponse:
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


@router.get("/api/review/{settlement_id}")
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
