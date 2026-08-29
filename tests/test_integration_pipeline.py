"""
Integration tests: verify the full agentic pipeline produces valid,
schema-compliant output end-to-end.

Tests the chain: EvidencePacket → Agent Loop → InvestigationResult → ReconciliationResult
"""

import json
import time
from datetime import date, datetime

import pytest

from backend.ai_investigator import (
    DemoLLMClient,
    MockLLMClient,
    investigate,
    MAX_AGENT_ITERATIONS,
    AUTO_RESOLVE_CONFIDENCE_THRESHOLD,
    verify_utr_cross_source,
    calculate_expected_fee,
    check_gst_compliance,
)
from backend.engine import run_engine
from backend.evaluation import evaluate_batch, EvaluationMetrics
from backend.ingestion import ingest_csvs
from backend.models import (
    AIClassification,
    AIRecommendedAction,
    AIResponse,
    AgentResponse,
    ConfidenceTier,
    DecisionState,
    EvidencePacket,
    LinkedPaymentsSummary,
    LinkedRefundsSummary,
    FeesSummary,
    TaxSummary,
    BankCreditEvidence,
    TimingEvidence,
    PaymentMethod,
    ValidationResult,
    ReconciliationResult,
)


# ---------------------------------------------------------------------------
# EvidencePacket → InvestigationResult pipeline
# ---------------------------------------------------------------------------

def _make_packet(
    settlement_id: str = "SETL_INT_001",
    expected: int = 100000,
    actual: int = 95000,
    failed: list[str] | None = None,
) -> EvidencePacket:
    return EvidencePacket(
        settlement_id=settlement_id,
        expected_amount_paise=expected,
        actual_amount_paise=actual,
        difference_paise=actual - expected,
        linked_payments_summary=LinkedPaymentsSummary(
            count=2, total_paise=150000, methods=[PaymentMethod.UPI, PaymentMethod.CARD],
        ),
        linked_refunds_summary=LinkedRefundsSummary(count=0, total_paise=0),
        fees_summary=FeesSummary(
            total_paise=3000, structure_applied="deterministic",
            validation_result=ValidationResult.PASSED,
        ),
        tax_summary=TaxSummary(
            total_paise=540, derivation_rule="floor(fee * 0.18)",
            validation_result=ValidationResult.PASSED,
        ),
        bank_credit=BankCreditEvidence(
            utr="UTR_INT", amount_paise=actual, date=date(2026, 8, 22),
        ),
        timing=TimingEvidence(
            settlement_created_at=datetime(2026, 8, 20, 10, 0, 0),
            settled_at=datetime(2026, 8, 21, 8, 0, 0),
            bank_credited_at=datetime(2026, 8, 22, 14, 30, 0),
            expected_cycle_days=2,
        ),
        deterministic_checks_passed=["schema_validation", "fee_validation"],
        deterministic_checks_failed=failed or [],
    )


class TestEndToEndPipeline:
    """Full pipeline: EvidencePacket → DemoLLMClient → InvestigationResult."""

    def test_demo_client_produces_valid_result(self):
        packet = _make_packet(failed=["MATH_DISCREPANCY"])
        result = investigate(packet, llm_client=DemoLLMClient())

        assert result.decision in (DecisionState.REVIEW_REQUIRED, DecisionState.AUTO_RESOLVED)
        assert result.ai_response is not None
        assert result.agent_response is not None
        assert result.confidence_tier in ("TIER_1", "TIER_2", "TIER_3")
        assert result.agent_iterations >= 1
        assert result.is_mock is True

    def test_mock_client_produces_valid_result(self):
        packet = _make_packet(failed=["MATH_DISCREPANCY"])
        client = MockLLMClient(
            classification="TIMING_MISMATCH",
            explanation="Bank credit delayed",
            confidence=0.85,
            cited_evidence=["timing"],
        )
        result = investigate(packet, llm_client=client)

        assert result.decision == DecisionState.REVIEW_REQUIRED
        assert result.ai_response is not None
        assert result.ai_response.classification == AIClassification.TIMING_MISMATCH
        assert result.ai_response.raw_confidence == 0.85
        assert result.confidence_tier == "TIER_2"
        assert result.agent_iterations >= 1

    def test_ai_response_matches_agent_response(self):
        packet = _make_packet(failed=["MATH_DISCREPANCY"])
        client = MockLLMClient(
            classification="UNEXPLAINED",
            explanation="Unexplained gap",
            confidence=0.6,
            cited_evidence=["timing"],
        )
        result = investigate(packet, llm_client=client)

        assert result.ai_response.classification == result.agent_response.classification
        assert result.ai_response.raw_confidence == result.agent_response.raw_confidence
        assert result.ai_response.explanation == result.agent_response.explanation

    def test_no_llm_returns_unresolved(self):
        packet = _make_packet(failed=["MATH_DISCREPANCY"])
        result = investigate(packet, llm_client=None)

        assert result.decision == DecisionState.UNRESOLVED
        assert result.error_type == "no_llm_client"
        assert result.ai_response is None

    def test_timeout_returns_unresolved(self):
        packet = _make_packet(failed=["MATH_DISCREPANCY"])
        client = MockLLMClient(fail_with="timeout")
        result = investigate(packet, llm_client=client)

        assert result.decision == DecisionState.UNRESOLVED
        assert result.error_type == "timeout"
        assert result.escalate_to_human is True

    def test_hallucinated_citation_returns_unresolved(self):
        packet = _make_packet(failed=["MATH_DISCREPANCY"])
        client = MockLLMClient(
            classification="UNEXPLAINED",
            explanation="Test",
            confidence=0.5,
            cited_evidence=["COMPLETELY_FAKE_EVIDENCE"],
        )
        result = investigate(packet, llm_client=client)

        assert result.decision == DecisionState.UNRESOLVED
        assert result.error_type == "hallucinated_evidence"
        assert result.ai_response is not None


# ---------------------------------------------------------------------------
# Tool integration
# ---------------------------------------------------------------------------

class TestToolIntegration:
    """Verify tools produce valid output from real EvidencePacket."""

    def test_verify_utr_tool(self):
        packet = _make_packet(actual=95000)
        result = verify_utr_cross_source(packet)

        assert result["tool"] == "verify_utr_cross_source"
        assert result["result"] == "CONSISTENT"
        assert result["settlement_id"] == "SETL_INT_001"

    def test_calculate_fee_tool(self):
        result = calculate_expected_fee(100000, "card")

        assert result["expected_fee_paise"] == 2100
        assert result["expected_tax_paise"] == 378
        assert result["total_expected_deduction_paise"] == 2478

    def test_check_gst_tool(self):
        result = check_gst_compliance(2100, 378)

        assert result["compliant"] is True
        assert result["difference_paise"] == 0


# ---------------------------------------------------------------------------
# Engine → AI → Evaluation integration
# ---------------------------------------------------------------------------

class TestEngineToEvaluation:
    """Verify engine output feeds correctly into evaluation harness."""

    def test_evaluation_computes_metrics(self):
        ing = ingest_csvs(
            transactions_path="data/evaluation/transactions.csv",
            settlements_path="data/evaluation/settlements.csv",
            refunds_path="data/evaluation/refunds.csv",
            bank_credits_path="data/evaluation/bank_credits.csv",
        )

        with open("data/evaluation/ground_truth.json") as f:
            gt = json.load(f)

        start = time.time()
        results = run_engine(
            ing.transactions, ing.settlements, ing.refunds, ing.bank_credits,
        )
        elapsed = time.time() - start

        metrics = evaluate_batch(results, gt, batch_time_seconds=elapsed)

        assert isinstance(metrics, EvaluationMetrics)
        assert metrics.total == 80
        assert metrics.match_rate >= 0.80
        assert metrics.throughput_per_second >= 1000
        assert metrics.ai_auto_approval_rate_pct == 0.0
        assert len(metrics.label_counts) == 12

    def test_per_class_metrics_present(self):
        ing = ingest_csvs(
            transactions_path="data/evaluation/transactions.csv",
            settlements_path="data/evaluation/settlements.csv",
            refunds_path="data/evaluation/refunds.csv",
            bank_credits_path="data/evaluation/bank_credits.csv",
        )

        with open("data/evaluation/ground_truth.json") as f:
            gt = json.load(f)

        results = run_engine(
            ing.transactions, ing.settlements, ing.refunds, ing.bank_credits,
        )
        metrics = evaluate_batch(results, gt, batch_time_seconds=0.01)

        # All 12 classes should have metrics
        for label in [
            "clean_match", "missing_reference", "bank_mismatch",
            "fee_mismatch", "tax_inconsistency", "refund_timing",
            "duplicate_detection", "adjustment_entry", "partial_settlement",
            "refund_after_settlement", "timing_race", "unexplained",
        ]:
            assert label in metrics.class_metrics, f"Missing class metrics for {label}"
            cm = metrics.class_metrics[label]
            assert cm.support > 0


# ---------------------------------------------------------------------------
# Safety invariants
# ---------------------------------------------------------------------------

class TestSafetyInvariants:
    """Verify critical safety properties hold at the type level."""

    def test_auto_approved_by_ai_always_zero(self):
        result = ReconciliationResult(
            settlement_id="test",
            decision=DecisionState.CLEAN_MATCH,
            difference_paise=0,
            expected_amount_paise=100000,
            actual_amount_paise=100000,
            deterministic_checks_passed=["schema_validation"],
            deterministic_checks_failed=[],
            escalate_to_human=False,
            auto_approved_by_ai=0,
        )
        assert result.auto_approved_by_ai == 0

    def test_auto_approved_by_ai_rejects_nonzero(self):
        with pytest.raises(Exception):
            ReconciliationResult(
                settlement_id="test",
                decision=DecisionState.CLEAN_MATCH,
                difference_paise=0,
                expected_amount_paise=100000,
                actual_amount_paise=100000,
                deterministic_checks_passed=["schema_validation"],
                deterministic_checks_failed=[],
                escalate_to_human=False,
                auto_approved_by_ai=1,
            )

    def test_ai_response_rejects_extra_fields(self):
        with pytest.raises(Exception):
            AIResponse(
                classification="UNEXPLAINED",
                explanation="Test",
                raw_confidence=0.5,
                cited_evidence=["timing"],
                recommended_action=AIRecommendedAction.ESCALATE_TO_HUMAN,
                forbidden_field="injection",  # extra="forbid"
            )

    def test_max_iterations_bounded(self):
        assert MAX_AGENT_ITERATIONS == 3

    def test_auto_resolve_threshold(self):
        assert AUTO_RESOLVE_CONFIDENCE_THRESHOLD == 0.95

    def test_confidence_tier_enforcement(self):
        packet = _make_packet(failed=["MATH_DISCREPANCY"])
        client = MockLLMClient(
            classification="UNEXPLAINED",
            explanation="Test",
            confidence=0.5,
            cited_evidence=["timing"],
        )
        result = investigate(packet, llm_client=client)
        assert result.confidence_tier == "TIER_3"

        client_high = MockLLMClient(
            classification="UNEXPLAINED",
            explanation="Test",
            confidence=0.96,
            cited_evidence=["timing"],
        )
        result_high = investigate(packet, llm_client=client_high)
        assert result_high.confidence_tier == "TIER_1"
