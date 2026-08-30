"""
Tests for Agentic AI Finance Controller features.

Covers:
- Agent tools (verify_utr_cross_source, calculate_expected_fee, etc.)
- Confidence tiers (TIER_1, TIER_2, TIER_3)
- Agent trace and reasoning steps
- Human review API
- Audit persistence for human reviews
- Agentic evaluation metrics
- Edge cases: auto-resolve eligibility, max iterations, citation validation
"""

import pytest
from datetime import datetime, date
from uuid import uuid4

from backend.ai_investigator import (
    investigate,
    compute_confidence_tier,
    validate_citations,
    LLMTimeoutError,
    LLMAPIError,
    LLMMalformedResponseError,
    verify_utr_cross_source,
    calculate_expected_fee,
    check_gst_compliance,
    query_batch_pattern,
    request_human_escalation,
    auto_resolve_trivial,
    _is_trivial_auto_resolve,
    InvestigationResult,
    MAX_AGENT_ITERATIONS,
    AUTO_RESOLVE_CONFIDENCE_THRESHOLD,
)
from tests.mocks import MockLLMClient
from backend.models import (
    AIClassification,
    AIRecommendedAction,
    AIResponse,
    AgentActionType,
    AgentResponse,
    AgentTrace,
    ConfidenceTier,
    DecisionState,
    EvidencePacket,
    FeesSummary,
    HumanReviewDecision,
    LinkedPaymentsSummary,
    LinkedRefundsSummary,
    PaymentMethod,
    PaymentDetail,
    ResolutionStatus,
    TaxSummary,
    TimingEvidence,
    BankCreditEvidence,
    ReconciliationResult,
    ReasoningStep,
    ToolCall,
    ToolResult,
    ValidationResult,
    CrossSettlementContext,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_evidence(
    settlement_id: str = "SETL_0001",
    expected: int = 100000,
    actual: int = 95000,
    failed_checks: list[str] = None,
    passed_checks: list[str] = None,
    refunds_count: int = 0,
    refunds_total: int = 0,
    payment_details: list[PaymentDetail] = None,
    cross_settlement: CrossSettlementContext = None,
) -> EvidencePacket:
    return EvidencePacket(
        settlement_id=settlement_id,
        expected_amount_paise=expected,
        actual_amount_paise=actual,
        difference_paise=actual - expected,
        linked_payments_summary=LinkedPaymentsSummary(
            count=2, total_paise=150000, methods=[PaymentMethod.UPI, PaymentMethod.CARD],
        ),
        linked_refunds_summary=LinkedRefundsSummary(count=refunds_count, total_paise=refunds_total),
        fees_summary=FeesSummary(
            total_paise=3000, structure_applied="card: floor(amount*0.02)+100",
            validation_result=ValidationResult.PASSED,
        ),
        tax_summary=TaxSummary(
            total_paise=540, derivation_rule="floor(fee * 0.18)",
            validation_result=ValidationResult.PASSED,
        ),
        bank_credit=BankCreditEvidence(
            utr="UTR_TEST", amount_paise=actual, date=date(2026, 8, 22),
        ),
        timing=TimingEvidence(
            settlement_created_at=datetime(2026, 8, 20, 10, 0, 0),
            settled_at=datetime(2026, 8, 21, 8, 0, 0),
            bank_credited_at=datetime(2026, 8, 22, 14, 30, 0),
            expected_cycle_days=2,
        ),
        deterministic_checks_passed=passed_checks or ["schema_validation", "fee_validation"],
        deterministic_checks_failed=failed_checks if failed_checks is not None else [],
        payment_details=payment_details or [],
        cross_settlement=cross_settlement,
    )


# ---------------------------------------------------------------------------
# Confidence Tier Tests
# ---------------------------------------------------------------------------

class TestConfidenceTierNew:
    def test_tier_1_threshold(self):
        assert compute_confidence_tier(0.95) == "TIER_1"
        assert compute_confidence_tier(1.0) == "TIER_1"

    def test_tier_2_threshold(self):
        assert compute_confidence_tier(0.80) == "TIER_2"
        assert compute_confidence_tier(0.94) == "TIER_2"

    def test_tier_3_threshold(self):
        assert compute_confidence_tier(0.79) == "TIER_3"
        assert compute_confidence_tier(0.0) == "TIER_3"

    def test_tier_boundary_values(self):
        assert compute_confidence_tier(0.949) == "TIER_2"
        assert compute_confidence_tier(0.95) == "TIER_1"
        assert compute_confidence_tier(0.799) == "TIER_3"
        assert compute_confidence_tier(0.80) == "TIER_2"


# ---------------------------------------------------------------------------
# Agent Tool Tests
# ---------------------------------------------------------------------------

class TestAgentTools:
    def test_verify_utr_cross_source_consistent(self):
        ep = _make_evidence(actual=95000)
        result = verify_utr_cross_source(ep)
        assert result["tool"] == "verify_utr_cross_source"
        assert result["result"] == "CONSISTENT"
        assert result["amount_match"] is True

    def test_verify_utr_cross_source_mismatch(self):
        ep = _make_evidence(actual=95000)
        ep.bank_credit = BankCreditEvidence(
            utr="UTR_TEST", amount_paise=90000, date=date(2026, 8, 22),
        )
        result = verify_utr_cross_source(ep)
        assert result["result"] == "MISMATCH"
        assert result["amount_match"] is False

    def test_calculate_expected_fee_upi(self):
        result = calculate_expected_fee(100000, "upi")
        assert result["expected_fee_paise"] == 0
        assert result["expected_tax_paise"] == 0
        assert result["total_expected_deduction_paise"] == 0

    def test_calculate_expected_fee_card(self):
        result = calculate_expected_fee(100000, "card")
        assert result["expected_fee_paise"] == 2100
        assert result["expected_tax_paise"] == 378
        assert result["total_expected_deduction_paise"] == 2478

    def test_calculate_expected_fee_netbanking(self):
        result = calculate_expected_fee(100000, "netbanking")
        assert result["expected_fee_paise"] == 1600
        assert result["expected_tax_paise"] == 288

    def test_calculate_expected_fee_unknown_method(self):
        result = calculate_expected_fee(100000, "crypto")
        assert "error" in result

    def test_check_gst_compliance_pass(self):
        result = check_gst_compliance(2100, 378)
        assert result["compliant"] is True
        assert result["difference_paise"] == 0

    def test_check_gst_compliance_fail(self):
        result = check_gst_compliance(2100, 400)
        assert result["compliant"] is False
        assert result["difference_paise"] == 22

    def test_query_batch_pattern_with_cross_settlement(self):
        cross = CrossSettlementContext(
            batch_size=100, batch_fee_exception_rate=0.15,
            batch_refund_rate=0.05, batch_math_discrepancy_rate=0.1,
            merchant_fee_exceptions_in_batch=15, method_mix={"upi": 60, "card": 40},
        )
        ep = _make_evidence(cross_settlement=cross)
        result = query_batch_pattern("fee_validation", ep)
        assert result["pattern_found"] is True
        assert result["batch_size"] == 100

    def test_query_batch_pattern_without_cross_settlement(self):
        ep = _make_evidence()
        result = query_batch_pattern("fee_validation", ep)
        assert result["pattern_found"] is False

    def test_request_human_escalation(self):
        ep = _make_evidence()
        result = request_human_escalation("Complex case", ep)
        assert result["escalated"] is True
        assert result["reason"] == "Complex case"

    def test_auto_resolve_trivial(self):
        result = auto_resolve_trivial({"settlement_id": "SETL_0001", "reason": "rounding"})
        assert result["auto_resolved"] is True
        assert result["requires_human_audit"] is True


# ---------------------------------------------------------------------------
# Auto-resolve Eligibility Tests
# ---------------------------------------------------------------------------

class TestAutoResolveEligibility:
    def test_off_by_one_paise_is_trivial(self):
        ep = _make_evidence(expected=100000, actual=100001)
        assert _is_trivial_auto_resolve(ep) is True

    def test_off_by_one_paise_negative_is_trivial(self):
        ep = _make_evidence(expected=100000, actual=99999)
        assert _is_trivial_auto_resolve(ep) is True

    def test_large_difference_not_trivial(self):
        ep = _make_evidence(expected=100000, actual=95000)
        assert _is_trivial_auto_resolve(ep) is False

    def test_per_payment_rounding_is_trivial(self):
        ep = _make_evidence(expected=100000, actual=99998)
        ep.linked_payments_summary = LinkedPaymentsSummary(
            count=2, total_paise=150000, methods=[PaymentMethod.UPI],
        )
        assert _is_trivial_auto_resolve(ep) is True


# ---------------------------------------------------------------------------
# Citation Validation Tests
# ---------------------------------------------------------------------------

class TestCitationValidationNew:
    def test_valid_citations(self):
        ep = _make_evidence(passed_checks=["fee_validation"], failed_checks=[])
        assert validate_citations(["timing", "fee_validation"], ep) is True

    def test_hallucinated_citation(self):
        ep = _make_evidence()
        assert validate_citations(["FAKE_EVIDENCE"], ep) is False

    def test_evidence_packet_id_citation(self):
        ep = _make_evidence()
        assert validate_citations([str(ep.evidence_packet_id)], ep) is True

    def test_empty_citations_valid(self):
        ep = _make_evidence()
        assert validate_citations([], ep) is True


# ---------------------------------------------------------------------------
# Investigation Result Tests
# ---------------------------------------------------------------------------

class TestInvestigationResult:
    def test_ai_response_always_set(self):
        """investigate() must set ai_response for successful cases."""
        ep = _make_evidence()
        client = MockLLMClient(
            classification="TIMING_MISMATCH", explanation="Bank delay",
            confidence=0.85, cited_evidence=["timing"],
        )
        result = investigate(ep, llm_client=client)
        assert result.ai_response is not None
        assert result.agent_response is not None

    def test_hallucinated_evidence_has_ai_response(self):
        """Hallucinated evidence case should still set ai_response."""
        ep = _make_evidence()
        client = MockLLMClient(
            classification="UNEXPLAINED", explanation="Test",
            confidence=0.5, cited_evidence=["FAKE_ID"],
        )
        result = investigate(ep, llm_client=client)
        assert result.decision == DecisionState.UNRESOLVED
        assert result.error_type == "hallucinated_evidence"
        assert result.ai_response is not None

    def test_timeout_no_ai_response(self):
        """Timeout should not set ai_response."""
        ep = _make_evidence()
        client = MockLLMClient(fail_with="timeout")
        result = investigate(ep, llm_client=client)
        assert result.ai_response is None
        assert result.error_type == "timeout"

    def test_agent_iterations_tracked(self):
        """Agent iterations should be tracked in result."""
        ep = _make_evidence()
        client = MockLLMClient(
            classification="UNEXPLAINED", explanation="Test",
            confidence=0.5, cited_evidence=["timing"],
        )
        result = investigate(ep, llm_client=client)
        assert result.agent_iterations >= 1
        assert result.agent_tool_calls >= 0

    def test_confidence_tier_matches_confidence(self):
        """Confidence tier should match the raw confidence value."""
        ep = _make_evidence()
        client = MockLLMClient(
            classification="UNEXPLAINED", explanation="Test",
            confidence=0.96, cited_evidence=["timing"],
        )
        result = investigate(ep, llm_client=client)
        assert result.confidence_tier == "TIER_1"

    def test_investigate_with_mock_client(self):
        """MockLLMClient should work for investigation."""
        ep = _make_evidence()
        result = investigate(ep, llm_client=MockLLMClient())
        assert result.decision in (
            DecisionState.REVIEW_REQUIRED, DecisionState.AUTO_RESOLVED,
        )
        assert result.is_mock is False

    def test_investigate_with_no_client(self):
        """No LLM client should return UNRESOLVED."""
        ep = _make_evidence()
        result = investigate(ep, llm_client=None)
        assert result.decision == DecisionState.UNRESOLVED
        assert result.error_type == "no_llm_client"

    def test_ai_never_produces_clean_match(self):
        """AI investigation should never produce CLEAN_MATCH."""
        ep = _make_evidence()
        client = MockLLMClient(
            classification="UNEXPLAINED", explanation="Test",
            confidence=0.5, cited_evidence=["timing"],
        )
        result = investigate(ep, llm_client=client)
        assert result.decision != DecisionState.CLEAN_MATCH


# ---------------------------------------------------------------------------
# Agent Response Model Tests
# ---------------------------------------------------------------------------

class TestAgentResponse:
    def test_agent_response_has_trace(self):
        """AgentResponse should include trace."""
        ep = _make_evidence()
        client = MockLLMClient(
            classification="TIMING_MISMATCH", explanation="Bank delay",
            confidence=0.85, cited_evidence=["timing"],
        )
        result = investigate(ep, llm_client=client)
        assert result.agent_response is not None
        assert result.agent_response.trace is not None
        assert result.agent_response.trace.settlement_id == "SETL_0001"

    def test_agent_response_classification_matches_ai_response(self):
        """AgentResponse and AIResponse classifications should match."""
        ep = _make_evidence()
        client = MockLLMClient(
            classification="REFUND_TIMING", explanation="Refund",
            confidence=0.7, cited_evidence=["timing"],
        )
        result = investigate(ep, llm_client=client)
        assert result.agent_response.classification == result.ai_response.classification
        assert result.agent_response.raw_confidence == result.ai_response.raw_confidence


# ---------------------------------------------------------------------------
# Human Review Decision Tests
# ---------------------------------------------------------------------------

class TestHumanReviewDecision:
    def test_human_review_decision_model(self):
        """HumanReviewDecision should be creatable."""
        review = HumanReviewDecision(
            settlement_id="SETL_0001",
            decision="APPROVE",
            reason="Verified manually",
            reviewer_id="reviewer_1",
        )
        assert review.settlement_id == "SETL_0001"
        assert review.decision == "APPROVE"
        assert review.reviewer_id == "reviewer_1"

    def test_human_review_reject(self):
        review = HumanReviewDecision(
            settlement_id="SETL_0002",
            decision="REJECT",
            reason="Suspicious transaction",
            reviewer_id="reviewer_2",
        )
        assert review.decision == "REJECT"


# ---------------------------------------------------------------------------
# Resolution Status Tests
# ---------------------------------------------------------------------------

class TestResolutionStatus:
    def test_resolution_status_open(self):
        result = ReconciliationResult(
            settlement_id="SETL_0001",
            decision=DecisionState.CLEAN_MATCH,
            difference_paise=0,
            expected_amount_paise=100000,
            actual_amount_paise=100000,
            deterministic_checks_passed=["schema_validation"],
            deterministic_checks_failed=[],
            escalate_to_human=False,
        )
        assert result.resolution_status == ResolutionStatus.OPEN

    def test_resolution_status_auto_approved_by_ai_always_zero(self):
        """auto_approved_by_ai must always be 0 (type-level enforced)."""
        result = ReconciliationResult(
            settlement_id="SETL_0001",
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


# ---------------------------------------------------------------------------
# Audit Logger Human Review Tests
# ---------------------------------------------------------------------------

class TestAuditHumanReview:
    def test_log_human_review_in_memory(self):
        from backend.audit import InMemoryAuditLogger

        logger = InMemoryAuditLogger()
        review = HumanReviewDecision(
            settlement_id="SETL_0001",
            decision="APPROVE",
            reason="Verified",
            reviewer_id="reviewer_1",
        )
        record = logger.log_human_review("SETL_0001", review)
        assert record.upload_hash == "human_review"
        assert record.settlement_id == "SETL_0001"
        assert record.decision_state == "RESOLVED_BY_HUMAN"

    def test_log_human_review_reject(self):
        from backend.audit import InMemoryAuditLogger

        logger = InMemoryAuditLogger()
        review = HumanReviewDecision(
            settlement_id="SETL_0001",
            decision="REJECT",
            reason="Suspicious",
            reviewer_id="reviewer_1",
        )
        record = logger.log_human_review("SETL_0001", review)
        assert record.decision_state == "REJECTED"

    def test_log_human_review_persistence(self):
        from backend.audit import InMemoryAuditLogger

        logger = InMemoryAuditLogger()
        review = HumanReviewDecision(
            settlement_id="SETL_0001",
            decision="APPROVE",
            reason="OK",
            reviewer_id="reviewer_1",
        )
        record = logger.log_human_review("SETL_0001", review)
        payload = record.payload()
        assert payload["human_review"]["decision"] == "APPROVE"
        assert payload["human_review"]["reviewer_id"] == "reviewer_1"


# ---------------------------------------------------------------------------
# Evaluation Metrics Agentic Tests
# ---------------------------------------------------------------------------

class TestEvaluationAgenticMetrics:
    def test_agentic_metrics_computed(self):
        from backend.evaluation import evaluate_batch, EvaluationMetrics

        results = [
            ReconciliationResult(
                settlement_id="SETL_0001",
                decision=DecisionState.REVIEW_REQUIRED,
                difference_paise=-5000,
                expected_amount_paise=100000,
                actual_amount_paise=95000,
                deterministic_checks_passed=["schema_validation"],
                deterministic_checks_failed=["MATH_DISCREPANCY"],
                escalate_to_human=True,
                agent_iterations=2,
                agent_tool_calls=1,
            ),
            ReconciliationResult(
                settlement_id="SETL_0002",
                decision=DecisionState.CLEAN_MATCH,
                difference_paise=0,
                expected_amount_paise=100000,
                actual_amount_paise=100000,
                deterministic_checks_passed=["schema_validation"],
                deterministic_checks_failed=[],
                escalate_to_human=False,
            ),
        ]

        ground_truth = [
            {"settlement_id": "SETL_0001", "label": "unexplained"},
            {"settlement_id": "SETL_0002", "label": "clean_match"},
        ]

        metrics = evaluate_batch(results, ground_truth)
        assert isinstance(metrics, EvaluationMetrics)
        assert metrics.total == 2
        assert metrics.pending_review_count == 1

    def test_auto_resolution_rate(self):
        from backend.evaluation import evaluate_batch

        results = [
            ReconciliationResult(
                settlement_id="SETL_0001",
                decision=DecisionState.AUTO_RESOLVED,
                difference_paise=-1,
                expected_amount_paise=100000,
                actual_amount_paise=99999,
                deterministic_checks_passed=["schema_validation"],
                deterministic_checks_failed=[],
                escalate_to_human=False,
                agent_iterations=1,
                agent_tool_calls=0,
            ),
        ]

        ground_truth = [{"settlement_id": "SETL_0001", "label": "clean_match"}]
        metrics = evaluate_batch(results, ground_truth)
        assert metrics.auto_resolution_count == 1
        assert metrics.auto_resolution_rate == 1.0

    def test_loop_closure_rate(self):
        from backend.evaluation import evaluate_batch

        results = [
            ReconciliationResult(
                settlement_id="SETL_0001",
                decision=DecisionState.AUTO_RESOLVED,
                difference_paise=-1,
                expected_amount_paise=100000,
                actual_amount_paise=99999,
                deterministic_checks_passed=["schema_validation"],
                deterministic_checks_failed=[],
                escalate_to_human=False,
                agent_iterations=1,
                agent_tool_calls=0,
            ),
            ReconciliationResult(
                settlement_id="SETL_0002",
                decision=DecisionState.REVIEW_REQUIRED,
                difference_paise=-5000,
                expected_amount_paise=100000,
                actual_amount_paise=95000,
                deterministic_checks_passed=["schema_validation"],
                deterministic_checks_failed=["MATH_DISCREPANCY"],
                escalate_to_human=True,
                agent_iterations=1,
                agent_tool_calls=0,
            ),
        ]

        ground_truth = [
            {"settlement_id": "SETL_0001", "label": "clean_match"},
            {"settlement_id": "SETL_0002", "label": "unexplained"},
        ]

        metrics = evaluate_batch(results, ground_truth)
        assert metrics.loop_closure_rate == 0.5  # 1 closed out of 2


# ---------------------------------------------------------------------------
# Max Iterations Tests
# ---------------------------------------------------------------------------

class TestMaxIterations:
    def test_max_iterations_constant(self):
        assert MAX_AGENT_ITERATIONS == 3

    def test_auto_resolve_threshold(self):
        assert AUTO_RESOLVE_CONFIDENCE_THRESHOLD == 0.95


# ---------------------------------------------------------------------------
# MockLLMClient Agent Loop Tests
# ---------------------------------------------------------------------------

class TestMockLLMClientAgentLoop:
    def test_tool_calls_sequence(self):
        """MockLLMClient should handle tool call sequences."""
        from tests.mocks import MockLLMClient

        client = MockLLMClient(
            classification="UNEXPLAINED",
            explanation="Test",
            confidence=0.5,
            cited_evidence=["timing"],
            tool_calls=[
                {"name": "verify_utr_cross_source", "arguments": {}},
                None,  # No tool call on second call
            ],
        )
        ep = _make_evidence()
        result = investigate(ep, llm_client=client)
        assert result.decision == DecisionState.REVIEW_REQUIRED

    def test_call_history_tracked(self):
        """MockLLMClient should track call history."""
        from tests.mocks import MockLLMClient

        client = MockLLMClient(
            classification="UNEXPLAINED",
            explanation="Test",
            confidence=0.5,
            cited_evidence=["timing"],
        )
        ep = _make_evidence()
        investigate(ep, llm_client=client)
        assert len(client._call_history) == 1
        assert client._call_count == 1
