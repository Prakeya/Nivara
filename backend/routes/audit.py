"""GET /audit/{upload_hash}, GET /audit/{upload_hash}/verify, GET /settlement/{settlement_id}."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from backend.api_helpers import _get_audit_logger

router = APIRouter()


@router.get("/audit/{upload_hash}")
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


@router.get("/settlement/{settlement_id}")
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


@router.get("/audit/{upload_hash}/verify")
async def verify_audit_chain(upload_hash: str) -> JSONResponse:
    """Verify hash chain integrity for a batch. Returns verification result."""
    if not re.fullmatch(r"[0-9a-f]{64}", upload_hash):
        raise HTTPException(status_code=400, detail=f"Invalid upload hash format: {upload_hash}")
    audit = _get_audit_logger()
    result = audit.verify_chain(upload_hash)
    return JSONResponse(content=result)
