"""Shaping of ReconciliationResult objects into API response payloads.

Extracted from backend/main.py (Task 7 router split). Holds:
- `_result_to_dict`: single-result -> JSON-safe dict.
- `process_reconciliation_results`: guard/model-selection/AI-validation/audit
  pipeline applied to a batch of results.
- `compute_result_summary`: the shared counts (clean/exceptions/unresolved/
  math_discrepancies/ai_investigations/match_rate) used by both
  routes/upload.py and routes/razorpay.py so their responses carry the same
  field set for the same underlying reconciliation flow (Task 7, Code
  Quality fix — previously /api/reconcile-razorpay omitted `unresolved` and
  `ai_investigations`, and used a different exceptions/unresolved grouping
  than /upload).
"""

from __future__ import annotations

import logging
from typing import Any

from backend.ai_validator import AIValidator, ValidationResult
from backend import ai_investigator
from backend.audit import AuditLogger
from backend.deterministic_guard import DeterministicGuard
from backend.model_selector import ModelSelector
from backend.models import DecisionState, ReconciliationResult

logger = logging.getLogger("nivara.api")


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


def process_reconciliation_results(
    results: list[ReconciliationResult],
    audit: AuditLogger,
    upload_hash: str,
    ai_enabled: bool,
) -> list[ReconciliationResult]:
    """Apply guard, model selection, AI validation, and final audit logging."""
    for result in results:
        guard_decision = DeterministicGuard.route(result.decision)
        validation: ValidationResult | None = None
        ai_response = None

        if guard_decision.requires_ai and ai_enabled:
            evidence = result.evidence_packet
            model_name = ModelSelector.select(evidence)
            ai_response = ai_investigator.investigate_v2(
                evidence_packet_v2=evidence,
                expected_amount_paise=result.expected_amount_paise,
                actual_amount_paise=result.actual_amount_paise,
                difference_paise=result.difference_paise,
                model_name=model_name,
            ) if evidence is not None else None
            validation = AIValidator.validate(
                ai_response=ai_response,
                evidence_packet=evidence,
                expected_paise=result.expected_amount_paise,
                actual_paise=result.actual_amount_paise,
            )
            if validation.is_valid:
                result.ai_response = ai_response
                result.resolution_confidence = ai_response.raw_confidence if ai_response else None
                result.resolution_source = "ai"
                result.ai_mode = "live"
                result.decision = DecisionState.REVIEW_REQUIRED
            else:
                result.decision = DecisionState.UNRESOLVED
                result.escalate_to_human = True
                logger.warning(
                    "AI validation failed for %s: %s",
                    result.settlement_id,
                    validation.violations,
                )
        elif guard_decision.requires_ai:
            result.decision = DecisionState.UNRESOLVED
            result.escalate_to_human = True
            validation = ValidationResult(False, ["AI client is not configured"])

        audit.log_result(
            upload_hash,
            result,
            validation_result=validation,
            evidence_packet=result.evidence_packet,
        )

    return results


def compute_result_summary(results: list[ReconciliationResult]) -> dict[str, Any]:
    """Compute the shared count/rate fields for a batch of results.

    Used by both /upload and /api/reconcile-razorpay so the two endpoints,
    which drive the same underlying reconciliation flow, return the same
    field set (`clean_matches`, `math_discrepancies`, `exceptions`,
    `unresolved`, `ai_investigations`, `match_rate`).

    Counts — REVIEW_REQUIRED counts as exception (human-reviewable),
    matching the evaluation's definition of "correctly caught".
    """
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
    ai_investigations = sum(1 for r in results if r.ai_response is not None)
    match_rate = round(clean / len(results) * 100, 1) if results else 0.0

    return {
        "clean_matches": clean,
        "math_discrepancies": math_disc,
        "exceptions": exceptions,
        "unresolved": unresolved,
        "ai_investigations": ai_investigations,
        "match_rate": match_rate,
    }
