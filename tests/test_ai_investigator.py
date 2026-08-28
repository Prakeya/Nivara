"""
Phase 7 Tests: AI Investigator

Must pass: LLM timeout → UNRESOLVED. Hallucinated evidence → UNRESOLVED.
Valid evidence → correct classification. AI cannot alter expected_amount.
"""

from datetime import datetime, date

from backend.ai_investigator import (
    investigate,
    compute_confidence_tier,
    validate_citations,
    MockLLMClient,
    LLMTimeoutError,
    LLMAPIError,
    LLMMalformedResponseError,
    LLMError,
    InvestigationResult,
)
from backend.models import (
    AIClassification,
    AIResponse,
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
)
from uuid import uuid4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_evidence_packet(
    settlement_id: str = "SETL_0001",
    expected: int = 100000,
    actual: int = 95000,
) -> EvidencePacket:
    return EvidencePacket(
        settlement_id=settlement_id,
        expected_amount_paise=expected,
        actual_amount_paise=actual,
        difference_paise=actual - expected,
        linked_payments_summary=LinkedPaymentsSummary(
            count=2,
            total_paise=150000,
            methods=[PaymentMethod.UPI, PaymentMethod.CARD],
        ),
        linked_refunds_summary=LinkedRefundsSummary(count=0, total_paise=0),
        fees_summary=FeesSummary(
            total_paise=3000,
            structure_applied="card: floor(amount*0.02)+100",
            validation_result=ValidationResult.PASSED,
        ),
        tax_summary=TaxSummary(
            total_paise=540,
            derivation_rule="floor(fee * 0.18)",
            validation_result=ValidationResult.PASSED,
        ),
        bank_credit=BankCreditEvidence(
            utr="UTR_TEST",
            amount_paise=actual,
            date=date(2026, 8, 22),
        ),
        timing=TimingEvidence(
            settlement_created_at=datetime(2026, 8, 20, 10, 0, 0),
            settled_at=datetime(2026, 8, 21, 8, 0, 0),
            bank_credited_at=datetime(2026, 8, 22, 14, 30, 0),
            expected_cycle_days=2,
        ),
        deterministic_checks_passed=["schema_validation", "fee_validation"],
        deterministic_checks_failed=["MATH_DISCREPANCY"],
    )


# ---------------------------------------------------------------------------
# Confidence tier
# ---------------------------------------------------------------------------

class TestConfidenceTier:
    def test_high_tier(self):
        assert compute_confidence_tier(0.7) == "HIGH"
        assert compute_confidence_tier(0.9) == "HIGH"
        assert compute_confidence_tier(1.0) == "HIGH"

    def test_medium_tier(self):
        assert compute_confidence_tier(0.4) == "MEDIUM"
        assert compute_confidence_tier(0.6) == "MEDIUM"

    def test_low_tier(self):
        assert compute_confidence_tier(0.0) == "LOW"
        assert compute_confidence_tier(0.3) == "LOW"
        assert compute_confidence_tier(0.39) == "LOW"


# ---------------------------------------------------------------------------
# Citation validation
# ---------------------------------------------------------------------------

class TestCitationValidation:
    def test_valid_citations(self):
        ep = _make_evidence_packet()
        assert validate_citations(["timing", "fee_validation"], ep) is True

    def test_valid_packet_id(self):
        ep = _make_evidence_packet()
        assert validate_citations([str(ep.evidence_packet_id)], ep) is True

    def test_hallucinated_citation(self):
        ep = _make_evidence_packet()
        assert validate_citations(["FAKE_EVIDENCE"], ep) is False

    def test_empty_citations(self):
        ep = _make_evidence_packet()
        assert validate_citations([], ep) is True

    def test_mixed_valid_invalid(self):
        ep = _make_evidence_packet()
        assert validate_citations(["timing", "NONEXISTENT"], ep) is False


# ---------------------------------------------------------------------------
# LLM timeout → UNRESOLVED
# ---------------------------------------------------------------------------

class TestLLMTimeout:
    def test_timeout_returns_unresolved(self):
        client = MockLLMClient(fail_with="timeout")
        ep = _make_evidence_packet()
        result = investigate(ep, llm_client=client)
        assert result.decision == DecisionState.UNRESOLVED
        assert result.error_type == "timeout"
        assert result.escalate_to_human is True

    def test_api_error_returns_unresolved(self):
        client = MockLLMClient(fail_with="api_error")
        ep = _make_evidence_packet()
        result = investigate(ep, llm_client=client)
        assert result.decision == DecisionState.UNRESOLVED
        assert result.error_type == "api_error"

    def test_malformed_response_returns_unresolved(self):
        client = MockLLMClient(fail_with="malformed_json")
        ep = _make_evidence_packet()
        result = investigate(ep, llm_client=client)
        assert result.decision == DecisionState.UNRESOLVED
        assert result.error_type == "malformed_json"

    def test_no_llm_client_returns_unresolved(self):
        ep = _make_evidence_packet()
        result = investigate(ep, llm_client=None)
        assert result.decision == DecisionState.UNRESOLVED
        assert result.error_type == "no_llm_client"


# ---------------------------------------------------------------------------
# Hallucinated evidence → UNRESOLVED
# ---------------------------------------------------------------------------

class TestHallucinatedEvidence:
    def test_hallucinated_citation_returns_unresolved(self):
        client = MockLLMClient(
            classification="UNEXPLAINED",
            cited_evidence=["FAKE_PACKET_ID", "timing"],
        )
        ep = _make_evidence_packet()
        result = investigate(ep, llm_client=client)
        assert result.decision == DecisionState.UNRESOLVED
        assert result.error_type == "hallucinated_evidence"
        assert result.ai_response is not None

    def test_all_hallucinated_returns_unresolved(self):
        client = MockLLMClient(
            classification="UNEXPLAINED",
            cited_evidence=["COMPLETELY_FAKE"],
        )
        ep = _make_evidence_packet()
        result = investigate(ep, llm_client=client)
        assert result.decision == DecisionState.UNRESOLVED
        assert result.error_type == "hallucinated_evidence"


# ---------------------------------------------------------------------------
# Valid evidence → correct classification
# ---------------------------------------------------------------------------

class TestValidClassification:
    def test_timing_mismatch(self):
        ep = _make_evidence_packet()
        client = MockLLMClient(
            classification="TIMING_MISMATCH",
            explanation="Bank credit took 3 days instead of expected 2.",
            confidence=0.85,
            cited_evidence=["timing"],
        )
        result = investigate(ep, llm_client=client)
        assert result.decision == DecisionState.REVIEW_REQUIRED
        assert result.ai_response.classification == AIClassification.TIMING_MISMATCH
        assert result.confidence_tier == "HIGH"
        assert result.escalate_to_human is True

    def test_refund_timing(self):
        ep = _make_evidence_packet()
        client = MockLLMClient(
            classification="REFUND_TIMING",
            explanation="Refund created within 24h of settlement.",
            confidence=0.6,
            cited_evidence=["timing", "linked_refunds_summary"],
        )
        result = investigate(ep, llm_client=client)
        assert result.decision == DecisionState.REVIEW_REQUIRED
        assert result.ai_response.classification == AIClassification.REFUND_TIMING
        assert result.confidence_tier == "MEDIUM"

    def test_unexplained(self):
        ep = _make_evidence_packet()
        client = MockLLMClient(
            classification="UNEXPLAINED",
            explanation="No evidence supports any explanation.",
            confidence=0.3,
            cited_evidence=["timing"],
        )
        result = investigate(ep, llm_client=client)
        assert result.decision == DecisionState.REVIEW_REQUIRED
        assert result.ai_response.classification == AIClassification.UNEXPLAINED
        assert result.confidence_tier == "LOW"


# ---------------------------------------------------------------------------
# AI cannot alter expected_amount
# ---------------------------------------------------------------------------

class TestAICannotAlterAmounts:
    def test_ai_response_has_no_amount_fields(self):
        ep = _make_evidence_packet()
        client = MockLLMClient(
            classification="UNEXPLAINED",
            explanation="Test",
            confidence=0.5,
            cited_evidence=["timing"],
        )
        result = investigate(ep, llm_client=client)
        # AIResponse schema has no amount fields (extra="forbid")
        assert not hasattr(result.ai_response, "expected_amount_paise")
        assert not hasattr(result.ai_response, "actual_amount_paise")

    def test_evidence_packet_preserved(self):
        ep = _make_evidence_packet(expected=200000, actual=195000)
        client = MockLLMClient(
            classification="UNEXPLAINED",
            explanation="Test",
            confidence=0.5,
            cited_evidence=["timing"],
        )
        result = investigate(ep, llm_client=client)
        # Evidence packet amounts are untouched
        assert ep.expected_amount_paise == 200000
        assert ep.actual_amount_paise == 195000

    def test_investigation_result_preserves_amounts(self):
        ep = _make_evidence_packet(expected=300000, actual=290000)
        client = MockLLMClient(
            classification="TIMING_MISMATCH",
            explanation="Test",
            confidence=0.8,
            cited_evidence=["timing"],
        )
        result = investigate(ep, llm_client=client)
        # InvestigationResult doesn't have amount fields either
        assert not hasattr(result, "expected_amount_paise")
        assert not hasattr(result, "actual_amount_paise")


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------

class TestEscalation:
    def test_all_ai_cases_escalate(self):
        for cls in ["TIMING_MISMATCH", "REFUND_TIMING", "UNEXPLAINED"]:
            client = MockLLMClient(
                classification=cls,
                explanation="Test",
                confidence=0.5,
                cited_evidence=["timing"],
            )
            ep = _make_evidence_packet()
            result = investigate(ep, llm_client=client)
            assert result.escalate_to_human is True
            assert result.decision == DecisionState.REVIEW_REQUIRED

    def test_error_cases_escalate(self):
        for fail in ["timeout", "api_error", "malformed_json"]:
            client = MockLLMClient(fail_with=fail)
            ep = _make_evidence_packet()
            result = investigate(ep, llm_client=client)
            assert result.escalate_to_human is True
            assert result.decision == DecisionState.UNRESOLVED


# ---------------------------------------------------------------------------
# Response validation edge cases
# ---------------------------------------------------------------------------

class TestResponseValidation:
    def test_invalid_classification_string(self):
        client = MockLLMClient(classification="INVALID_CLASS")
        ep = _make_evidence_packet()
        result = investigate(ep, llm_client=client)
        assert result.decision == DecisionState.UNRESOLVED
        assert result.error_type == "malformed_json"

    def test_missing_explanation(self):
        client = MockLLMClient(explanation="")
        ep = _make_evidence_packet()
        result = investigate(ep, llm_client=client)
        assert result.decision == DecisionState.UNRESOLVED
        assert result.error_type == "malformed_json"

    def test_confidence_clamped(self):
        client = MockLLMClient(confidence=1.5)
        ep = _make_evidence_packet()
        result = investigate(ep, llm_client=client)
        # Confidence is clamped to [0.0, 1.0] per architecture spec
        assert result.decision == DecisionState.REVIEW_REQUIRED
        assert result.ai_response.raw_confidence == 1.0
        assert result.confidence_tier == "HIGH"


# ---------------------------------------------------------------------------
# MockLLMClient
# ---------------------------------------------------------------------------

class TestMockLLMClient:
    def test_returns_controlled_response(self):
        client = MockLLMClient(
            classification="TIMING_MISMATCH",
            explanation="Test explanation",
            confidence=0.8,
            cited_evidence=["timing"],
        )
        resp = client.complete([{"role": "user", "content": "test"}])
        assert resp["classification"] == "TIMING_MISMATCH"
        assert resp["raw_confidence"] == 0.8

    def test_tracks_call_count(self):
        client = MockLLMClient()
        assert client._call_count == 0
        client.complete([{"role": "user", "content": "test"}])
        assert client._call_count == 1
        client.complete([{"role": "user", "content": "test2"}])
        assert client._call_count == 2

    def test_fail_with_timeout(self):
        client = MockLLMClient(fail_with="timeout")
        try:
            client.complete([{"role": "user", "content": "test"}])
            assert False, "Should have raised"
        except LLMTimeoutError:
            pass

    def test_fail_with_api_error(self):
        client = MockLLMClient(fail_with="api_error")
        try:
            client.complete([{"role": "user", "content": "test"}])
            assert False, "Should have raised"
        except LLMAPIError:
            pass


# ---------------------------------------------------------------------------
# Full pipeline: engine → investigator
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_engine_exception_then_investigate(self):
        from backend.engine import reconcile_settlement

        settlement = {
            "settlement_id": "SETL_TEST",
            "amount": 95000,
            "linked_payment_ids": ["PAY1"],
            "linked_refund_ids": [],
            "utr": "UTR_TEST",
        }
        linked_payments = [{
            "payment_id": "PAY1",
            "amount": 100000,
            "fee": 2100,
            "tax": 378,
            "method": "card",
        }]
        result = reconcile_settlement(
            settlement=settlement,
            linked_payments=linked_payments,
            linked_refunds=[],
            bank_credit={"utr": "UTR_TEST", "amount": 95000, "date": date(2026, 8, 22)},
            linkage_errors=[],
            duplicate_errors=[],
        )
        assert result.decision == DecisionState.MATH_DISCREPANCY

        # Build evidence packet from engine result
        ep = EvidencePacket(
            settlement_id="SETL_TEST",
            expected_amount_paise=result.expected_amount_paise,
            actual_amount_paise=result.actual_amount_paise,
            difference_paise=result.difference_paise,
            linked_payments_summary=LinkedPaymentsSummary(
                count=1, total_paise=100000, methods=[PaymentMethod.CARD],
            ),
            linked_refunds_summary=LinkedRefundsSummary(count=0, total_paise=0),
            fees_summary=FeesSummary(
                total_paise=2100,
                structure_applied="card: floor(amount*0.02)+100",
                validation_result=ValidationResult.PASSED,
            ),
            tax_summary=TaxSummary(
                total_paise=378,
                derivation_rule="floor(fee * 0.18)",
                validation_result=ValidationResult.PASSED,
            ),
            bank_credit=BankCreditEvidence(
                utr="UTR_TEST", amount_paise=95000, date=date(2026, 8, 22),
            ),
            timing=TimingEvidence(
                settlement_created_at=datetime(2026, 8, 20, 10, 0, 0),
                settled_at=datetime(2026, 8, 21, 8, 0, 0),
                bank_credited_at=datetime(2026, 8, 22, 14, 30, 0),
                expected_cycle_days=2,
            ),
            deterministic_checks_passed=result.deterministic_checks_passed,
            deterministic_checks_failed=result.deterministic_checks_failed,
        )

        # Investigate
        client = MockLLMClient(
            classification="UNEXPLAINED",
            explanation="Unexplained discrepancy of 5000 paise.",
            confidence=0.45,
            cited_evidence=["timing"],
        )
        inv_result = investigate(ep, llm_client=client)
        assert inv_result.decision == DecisionState.REVIEW_REQUIRED
        assert inv_result.ai_response.classification == AIClassification.UNEXPLAINED
        assert inv_result.escalate_to_human is True
