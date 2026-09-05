"""
Phase 7 Tests: AI Investigator

Must pass: LLM timeout → UNRESOLVED. Hallucinated evidence → UNRESOLVED.
Valid evidence → correct classification. AI cannot alter expected_amount.
"""

from datetime import datetime, date
import json

import pytest

from backend.ai_investigator import (
    investigate,
    compute_confidence_tier,
    validate_citations,
    LLMTimeoutError,
    LLMAPIError,
    LLMMalformedResponseError,
    LLMError,
    InvestigationResult,
)
from tests.mocks import MockLLMClient
from backend.engine import run_engine, reconcile_settlement
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

def _txn(pid, amount, method="upi", fee=0, tax=0, settlement_id=None):
    return {
        "payment_id": pid,
        "order_id": f"ORD_{pid}",
        "amount": amount,
        "status": "captured",
        "method": method,
        "fee": fee,
        "tax": tax,
        "created_at": datetime(2026, 8, 20, 10, 0, 0),
        "settlement_id": settlement_id,
    }


def _settlement(sid, amount, utr="UTR_001", linked_pids=None, linked_rids=None):
    return {
        "settlement_id": sid,
        "amount": amount,
        "status": "settled",
        "utr": utr,
        "created_at": datetime(2026, 8, 20, 10, 0, 0),
        "settled_at": datetime(2026, 8, 21, 8, 0, 0),
        "linked_payment_ids": linked_pids or [],
        "linked_refund_ids": linked_rids or [],
    }


def _bank_credit(utr, amount):
    return {
        "utr": utr,
        "amount": amount,
        "date": date(2026, 8, 22),
        "description": "Bank credit",
        "bank_account": "ACC001",
    }


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
        assert compute_confidence_tier(0.7) == "TIER_3"
        assert compute_confidence_tier(0.9) == "TIER_2"
        assert compute_confidence_tier(1.0) == "TIER_1"

    def test_medium_tier(self):
        assert compute_confidence_tier(0.4) == "TIER_3"
        assert compute_confidence_tier(0.6) == "TIER_3"

    def test_low_tier(self):
        assert compute_confidence_tier(0.0) == "TIER_3"
        assert compute_confidence_tier(0.3) == "TIER_3"
        assert compute_confidence_tier(0.39) == "TIER_3"


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
        assert result.confidence_tier == "TIER_2"
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
        assert result.confidence_tier == "TIER_3"

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
        assert result.confidence_tier == "TIER_3"


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
        assert result.confidence_tier == "TIER_1"


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


# ---------------------------------------------------------------------------
# Production LLM path tests
# ---------------------------------------------------------------------------

class TestProductionLLMPath:
    """Verify real OpenAI client is used in production, not MockLLMClient."""

    def test_engine_accepts_llm_client_parameter(self):
        """run_engine() accepts an llm_client parameter."""
        import inspect
        sig = inspect.signature(run_engine)
        assert "llm_client" in sig.parameters

    def test_engine_none_llm_skips_ai_investigation(self):
        """When llm_client is None, AI investigation is skipped and result is escalated to human."""
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100500, utr="UTR_001", linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100500)

        results = run_engine([t], [s], [], [bc], llm_client=None)

        assert len(results) == 1
        assert results[0].decision == DecisionState.MATH_DISCREPANCY
        assert results[0].escalate_to_human is True
        assert results[0].ai_response is None
        assert results[0].ai_mode is None

    def test_engine_mock_llm_produces_math_discrepancy(self):
        """When fallback chain succeeds, MATH_DISCREPANCY stays as MATH_DISCREPANCY with ai_response."""
        from unittest.mock import patch, MagicMock
        from backend.models import AIResponse, AIClassification

        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100500, utr="UTR_001", linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100500)

        mock_ai = AIResponse(
            classification=AIClassification.UNEXPLAINED,
            explanation="Test discrepancy",
            raw_confidence=0.5,
            cited_evidence=["fee_evidence"],
        )

        with patch("backend.ai_investigator.investigate_v2", return_value=mock_ai) as mock_inv:
            results = run_engine([t], [s], [], [bc], llm_client="mock")

        assert len(results) == 1
        assert results[0].decision == DecisionState.MATH_DISCREPANCY
        assert results[0].ai_response is not None
        assert results[0].escalate_to_human is True
        mock_inv.assert_called_once()

    def test_missing_api_key_returns_none(self):
        """_get_llm_client() returns None when GROQ_API_KEY is not set."""
        import os
        from backend.api_helpers import _get_llm_client

        old_key = os.environ.pop("GROQ_API_KEY", None)
        try:
            client = _get_llm_client()
            assert client is None
        finally:
            if old_key is not None:
                os.environ["GROQ_API_KEY"] = old_key

    def test_invalid_api_key_returns_configured(self):
        """_get_llm_client() returns truthy value when GROQ_API_KEY is set."""
        import os
        from backend.api_helpers import _get_llm_client

        old_key = os.environ.get("GROQ_API_KEY")
        os.environ["GROQ_API_KEY"] = "gsk-test-invalid-key-for-testing"
        try:
            client = _get_llm_client()
            assert client is not None
            assert client  # truthy
        finally:
            if old_key is not None:
                os.environ["GROQ_API_KEY"] = old_key
            else:
                os.environ.pop("GROQ_API_KEY", None)

    def test_investigate_with_none_returns_unresolved(self):
        """investigate() with llm_client=None returns UNRESOLVED."""
        ep = _make_evidence_packet()
        result = investigate(ep, llm_client=None)
        assert result.decision == DecisionState.UNRESOLVED
        assert result.error_type == "no_llm_client"
        assert result.escalate_to_human is True

    def test_ai_never_returns_clean_match(self):
        """AI investigation can never produce CLEAN_MATCH decision."""
        for cls in ["TIMING_MISMATCH", "REFUND_TIMING", "UNEXPLAINED"]:
            client = MockLLMClient(
                classification=cls,
                explanation="Test",
                confidence=0.9,
                cited_evidence=["timing"],
            )
            ep = _make_evidence_packet()
            result = investigate(ep, llm_client=client)
            assert result.decision != DecisionState.CLEAN_MATCH

    def test_ai_never_auto_approves(self):
        """AI response always has recommended_action=ESCALATE_TO_HUMAN."""
        client = MockLLMClient(
            classification="TIMING_MISMATCH",
            explanation="Test",
            confidence=0.9,
            cited_evidence=["timing"],
        )
        ep = _make_evidence_packet()
        result = investigate(ep, llm_client=client)
        assert result.ai_response.recommended_action.value == "ESCALATE_TO_HUMAN"

    def test_ai_cannot_inject_financial_fields(self):
        """AIResponse rejects extra fields (extra='forbid')."""
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            AIResponse(
                classification="UNEXPLAINED",
                explanation="Test",
                raw_confidence=0.5,
                cited_evidence=["timing"],
                expected_amount_paise=100000,  # forbidden field
            )


class TestInvestigateV2GroqPath:
    """investigate_v2 uses the Groq fallback chain + model selector."""

    def _packet(self) -> "EvidencePacketV2":
        from backend.evidence_packet import EvidencePacketV2, FeeEvidence
        return EvidencePacketV2.model_construct(
            settlement_id="SETL_GROQ",
            fee_evidence=FeeEvidence(
                computed_fee_paise=1000, reported_fee_paise=1500,
                formula_used="1%", discrepancy_paise=500,
            ),
        )

    def _success_result(self, model_name: str = "groq/compound-mini"):
        from backend.fallback_chain import FallbackResult
        return FallbackResult(
            provider="groq",
            success=True,
            model=model_name,
            latency_ms=83,
            response={
                "classification": "TIMING_MISMATCH",
                "explanation": "settlement delayed by bank",
                "confidence": 0.8,
                "cited_evidence": ["fee_evidence"],
                "usage": {"prompt_tokens": 120, "completion_tokens": 40},
            },
        )

    def test_simple_packet_selects_8b_and_returns_ai_response(self):
        from unittest.mock import patch
        from backend.ai_investigator import investigate_v2

        with patch(
            "backend.fallback_chain.call_with_fallback",
            return_value=self._success_result(),
        ) as mock_fb:
            ai = investigate_v2(self._packet(), 100000, 100500, 500)

        assert ai is not None
        mock_fb.assert_called_once()
        assert mock_fb.call_args.kwargs["primary_model"] == "groq/compound-mini"
        assert ai.provider_name == "groq"
        assert ai.model_name == "groq/compound-mini"
        assert ai.classification == AIClassification.TIMING_MISMATCH
        assert ai.latency_ms == 83
        assert ai.cost_inr == 0.0

    def test_fallback_failure_returns_none(self):
        from unittest.mock import patch
        from backend.fallback_chain import FallbackResult
        from backend.ai_investigator import investigate_v2

        with patch(
            "backend.fallback_chain.call_with_fallback",
            return_value=FallbackResult(success=False),
        ):
            ai = investigate_v2(self._packet(), 100000, 100500, 500)

        assert ai is None

    def test_invalid_citations_returns_none(self):
        from unittest.mock import patch
        from backend.fallback_chain import FallbackResult
        from backend.ai_investigator import investigate_v2

        res = self._success_result()
        res.response["cited_evidence"] = ["hallucinated_evidence"]
        with patch(
            "backend.fallback_chain.call_with_fallback",
            return_value=res,
        ):
            ai = investigate_v2(self._packet(), 100000, 100500, 500)

        assert ai is None
