from unittest.mock import patch

from backend.audit import AuditLogger
from backend.main import process_reconciliation_results
from backend.models import (
    AIClassification,
    AIResponse,
    DecisionState,
    ReconciliationResult,
)
from tests.test_evidence_packet import _make_full_packet


def _result() -> ReconciliationResult:
    return ReconciliationResult(
        settlement_id="SETL_WIRING",
        decision=DecisionState.MATH_DISCREPANCY,
        difference_paise=100,
        expected_amount_paise=99900,
        actual_amount_paise=100000,
        deterministic_checks_passed=["references_exist"],
        deterministic_checks_failed=[],
        escalate_to_human=True,
        evidence_packet=_make_full_packet(),
    )


def _ai_response() -> AIResponse:
    return AIResponse(
        classification=AIClassification.TIMING_MISMATCH,
        explanation="Timing evidence explains the discrepancy.",
        raw_confidence=0.85,
        cited_evidence=["timing_evidence"],
    )


def test_process_results_routes_valid_ai_to_review(tmp_path):
    audit = AuditLogger(str(tmp_path / "audit.db"))
    with patch("backend.main.ModelSelector.select", return_value="test-model") as select_model, patch(
        "backend.main.ai_investigator.investigate_v2", return_value=_ai_response()
    ) as investigate:
        results = process_reconciliation_results(
            [_result()], audit, "a" * 64, ai_enabled=True
        )

    assert results[0].decision == DecisionState.REVIEW_REQUIRED
    assert results[0].ai_response is not None
    select_model.assert_called_once()
    investigate.assert_called_once_with(
        evidence_packet_v2=results[0].evidence_packet,
        expected_amount_paise=99900,
        actual_amount_paise=100000,
        difference_paise=100,
        model_name="test-model",
    )
    assert audit.get_batch("a" * 64)[0].payload()["validation_result"]["is_valid"] is True
    audit.close()


def test_process_results_routes_invalid_ai_to_unresolved(tmp_path):
    audit = AuditLogger(str(tmp_path / "audit.db"))
    with patch(
        "backend.main.ai_investigator.investigate_v2",
        return_value=None,
    ):
        results = process_reconciliation_results(
            [_result()], audit, "b" * 64, ai_enabled=True
        )

    assert results[0].decision == DecisionState.UNRESOLVED
    assert results[0].ai_response is None
    validation = audit.get_batch("b" * 64)[0].payload()["validation_result"]
    assert validation["is_valid"] is False
    assert "AI response is missing" in validation["violations"]
    audit.close()
