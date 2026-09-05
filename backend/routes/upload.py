"""POST /upload — accept 4 CSVs, run reconciliation, return job_id."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from backend.api_helpers import _compute_hash, _get_audit_logger, _get_client_ip, _get_llm_client
from backend.batch_analyzer import analyze_batch
from backend.ingestion import IngestionResult, ingest_csvs
from backend.engine import run_engine
from backend.job_store import MAX_JOBS, JobResult, _jobs, _jobs_lock, _rate_limiter
from backend.rbac import require_upload
from backend.response_shaping import _result_to_dict, compute_result_summary, process_reconciliation_results

logger = logging.getLogger("nivara.api")

router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
CSV_MAGIC_BYTES = [b"application/csv", b"text/csv", b"application/vnd.ms-excel"]


@router.post("/upload")
async def upload_files(
    request: Request,
    transactions: UploadFile = File(...),
    settlements: UploadFile = File(...),
    refunds: UploadFile = File(...),
    bank_credits: UploadFile = File(...),
    _auth: None = Depends(require_upload),
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

        audit = _get_audit_logger()
        with _jobs_lock:
            cached_job = next(
                (existing for existing in _jobs.values()
                 if existing.upload_hash == upload_hash
                 and existing.status == "completed"
                 and audit.total_records(upload_hash) > 0),
                None,
            )
        if cached_job is not None:
            logger.info("Duplicate upload detected: %s", upload_hash)
            return JSONResponse(status_code=202, content={
                "job_id": cached_job.job_id,
                "upload_hash": upload_hash,
                "status": "completed",
                "message": "Batch already processed. Returning cached results.",
            })

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
            llm_client=None,
        )
        results = process_reconciliation_results(
            results,
            audit=audit,
            upload_hash=upload_hash,
            ai_enabled=llm_client is not None,
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
        audit_records = [r.to_dict() for r in audit.get_batch(upload_hash)]

        # Shared summary counts (unresolved / ai_investigations / etc.) — see
        # backend/response_shaping.py::compute_result_summary. Keeps this
        # response's field set identical to /api/reconcile-razorpay's.
        summary = compute_result_summary(results)
        clean = summary["clean_matches"]
        math_disc = summary["math_discrepancies"]
        exceptions = summary["exceptions"]
        unresolved = summary["unresolved"]
        ai_inv = summary["ai_investigations"]

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
            except FileNotFoundError:
                # Expected/benign: no ground truth file configured for this run.
                # Evaluation is optional — skip it quietly.
                logger.info("Ground truth file not found at %s; skipping evaluation", gt_path)
            except Exception:
                # Real failure: malformed JSON, or evaluate_batch raised. Non-fatal —
                # the reconciliation flow continues, but capture the traceback so it's
                # distinguishable from the expected-missing-file case above.
                logger.exception("Ground truth evaluation failed unexpectedly")

        job.status = "completed"
        job.total_settlements = len(results)
        job.clean_matches = clean
        job.exceptions = exceptions
        job.unresolved = unresolved
        job.math_discrepancies = math_disc
        job.ai_investigations = ai_inv
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
