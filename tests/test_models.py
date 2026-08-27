import pytest
from datetime import datetime, date
from pydantic import ValidationError

from backend.models import (
    Transaction,
    Settlement,
    Refund,
    BankCredit,
    EvidencePacket,
    AIResponse,
    ReconciliationResult,
    BatchMetrics,
    EvaluationResult,
    LinkedPaymentsSummary,
    LinkedRefundsSummary,
    FeesSummary,
    TaxSummary,
    BankCreditEvidence,
    TimingEvidence,
    TransactionStatus,
    PaymentMethod,
    SettlementStatus,
    RefundStatus,
    DecisionState,
    AIClassification,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _transaction(**overrides):
    defaults = dict(
        payment_id="PAY_001",
        order_id="ORD_001",
        amount=100000,
        status=TransactionStatus.CAPTURED,
        method=PaymentMethod.UPI,
        fee=0,
        tax=0,
        created_at=datetime(2026, 8, 20, 10, 0, 0),
    )
    defaults.update(overrides)
    return Transaction(**defaults)


def _settlement(**overrides):
    defaults = dict(
        settlement_id="SETL_001",
        amount=100000,
        status=SettlementStatus.SETTLED,
        utr="UTR123456",
        created_at=datetime(2026, 8, 20, 10, 0, 0),
        settled_at=datetime(2026, 8, 21, 8, 0, 0),
        linked_payment_ids=["PAY_001"],
        linked_refund_ids=[],
    )
    defaults.update(overrides)
    return Settlement(**defaults)


def _refund(**overrides):
    defaults = dict(
        refund_id="REF_001",
        payment_id="PAY_001",
        amount=5000,
        status=RefundStatus.PROCESSED,
        created_at=datetime(2026, 8, 20, 12, 0, 0),
    )
    defaults.update(overrides)
    return Refund(**defaults)


def _bank_credit(**overrides):
    defaults = dict(
        utr="UTR123456",
        amount=95000,
        date=date(2026, 8, 22),
        description="Settlement credit",
        bank_account="1234567890",
    )
    defaults.update(overrides)
    return BankCredit(**defaults)


def _linked_payments_summary(**overrides):
    defaults = dict(
        count=2,
        total_paise=1500000,
        methods=[PaymentMethod.UPI, PaymentMethod.CARD],
    )
    defaults.update(overrides)
    return LinkedPaymentsSummary(**defaults)


def _linked_refunds_summary(**overrides):
    defaults = dict(
        count=1,
        total_paise=200000,
    )
    defaults.update(overrides)
    return LinkedRefundsSummary(**defaults)


def _fees_summary(**overrides):
    defaults = dict(
        total_paise=30000,
        structure_applied="card: floor(amount*0.02)+100",
        validation_result=ValidationResult.PASSED,
    )
    defaults.update(overrides)
    return FeesSummary(**defaults)


def _tax_summary(**overrides):
    defaults = dict(
        total_paise=5400,
        derivation_rule="floor(fee * 0.18)",
        validation_result=ValidationResult.PASSED,
    )
    defaults.update(overrides)
    return TaxSummary(**defaults)


def _bank_credit_evidence(**overrides):
    defaults = dict(
        utr="UTR987654",
        amount_paise=1092500,
        date=date(2026, 8, 22),
    )
    defaults.update(overrides)
    return BankCreditEvidence(**defaults)


def _timing_evidence(**overrides):
    defaults = dict(
        settlement_created_at=datetime(2026, 8, 20, 10, 0, 0),
        settled_at=datetime(2026, 8, 21, 8, 0, 0),
        bank_credited_at=datetime(2026, 8, 22, 14, 30, 0),
        expected_cycle_days=2,
    )
    defaults.update(overrides)
    return TimingEvidence(**defaults)


def _evidence_packet(**overrides):
    defaults = dict(
        settlement_id="SETL_123",
        expected_amount_paise=1270000,
        actual_amount_paise=1092500,
        difference_paise=-177500,
        linked_payments_summary=_linked_payments_summary(),
        linked_refunds_summary=_linked_refunds_summary(),
        fees_summary=_fees_summary(),
        tax_summary=_tax_summary(),
        bank_credit=_bank_credit_evidence(),
        timing=_timing_evidence(),
        deterministic_checks_passed=["references_exist", "bank_match"],
        deterministic_checks_failed=[],
    )
    defaults.update(overrides)
    return EvidencePacket(**defaults)


def _ai_response(**overrides):
    defaults = dict(
        classification=AIClassification.TIMING_MISMATCH,
        explanation="Bank credited after expected cycle",
        raw_confidence=0.82,
        cited_evidence=["SETL_123", "timing"],
    )
    defaults.update(overrides)
    return AIResponse(**defaults)


def _reconciliation_result(**overrides):
    defaults = dict(
        settlement_id="SETL_001",
        decision=DecisionState.CLEAN_MATCH,
        difference_paise=0,
        expected_amount_paise=100000,
        actual_amount_paise=100000,
        ai_response=None,
        deterministic_checks_passed=["all_checks"],
        deterministic_checks_failed=[],
        escalate_to_human=False,
    )
    defaults.update(overrides)
    return ReconciliationResult(**defaults)


def _batch_metrics(**overrides):
    defaults = dict(
        total_settlements=60,
        clean_matches=34,
        exceptions=20,
        unresolved=6,
        auto_approved_by_ai=0,
        ai_investigations=8,
        ai_invocation_rate=0.133,
    )
    defaults.update(overrides)
    return BatchMetrics(**defaults)


def _evaluation_result(**overrides):
    defaults = dict(
        match_rate=0.717,
        false_accept_rate=0.05,
        safe_escalation_rate=0.333,
        ai_invocation_rate=0.133,
        ai_auto_approval_rate_pct=0.0,
        processing_time_per_settlement=0.8,
    )
    defaults.update(overrides)
    return EvaluationResult(**defaults)


# ===========================================================================
# 1. JSON round-trip
# ===========================================================================

class TestJsonRoundTrip:
    def test_transaction_round_trip(self):
        original = _transaction()
        restored = Transaction.model_validate_json(original.model_dump_json())
        assert original.model_dump() == restored.model_dump()

    def test_settlement_round_trip(self):
        original = _settlement()
        restored = Settlement.model_validate_json(original.model_dump_json())
        assert original.model_dump() == restored.model_dump()

    def test_refund_round_trip(self):
        original = _refund()
        restored = Refund.model_validate_json(original.model_dump_json())
        assert original.model_dump() == restored.model_dump()

    def test_bank_credit_round_trip(self):
        original = _bank_credit()
        restored = BankCredit.model_validate_json(original.model_dump_json())
        assert original.model_dump() == restored.model_dump()

    def test_evidence_packet_round_trip(self):
        original = _evidence_packet()
        restored = EvidencePacket.model_validate_json(original.model_dump_json())
        assert original.model_dump() == restored.model_dump()

    def test_ai_response_round_trip(self):
        original = _ai_response()
        restored = AIResponse.model_validate_json(original.model_dump_json())
        assert original.model_dump() == restored.model_dump()

    def test_reconciliation_result_round_trip(self):
        original = _reconciliation_result()
        restored = ReconciliationResult.model_validate_json(original.model_dump_json())
        assert original.model_dump() == restored.model_dump()

    def test_batch_metrics_round_trip(self):
        original = _batch_metrics()
        restored = BatchMetrics.model_validate_json(original.model_dump_json())
        assert original.model_dump() == restored.model_dump()

    def test_evaluation_result_round_trip(self):
        original = _evaluation_result()
        restored = EvaluationResult.model_validate_json(original.model_dump_json())
        assert original.model_dump() == restored.model_dump()

    def test_linked_payments_summary_round_trip(self):
        original = _linked_payments_summary()
        restored = LinkedPaymentsSummary.model_validate_json(original.model_dump_json())
        assert original.model_dump() == restored.model_dump()

    def test_linked_refunds_summary_round_trip(self):
        original = _linked_refunds_summary()
        restored = LinkedRefundsSummary.model_validate_json(original.model_dump_json())
        assert original.model_dump() == restored.model_dump()

    def test_fees_summary_round_trip(self):
        original = _fees_summary()
        restored = FeesSummary.model_validate_json(original.model_dump_json())
        assert original.model_dump() == restored.model_dump()

    def test_tax_summary_round_trip(self):
        original = _tax_summary()
        restored = TaxSummary.model_validate_json(original.model_dump_json())
        assert original.model_dump() == restored.model_dump()

    def test_bank_credit_evidence_round_trip(self):
        original = _bank_credit_evidence()
        restored = BankCreditEvidence.model_validate_json(original.model_dump_json())
        assert original.model_dump() == restored.model_dump()

    def test_timing_evidence_round_trip(self):
        original = _timing_evidence()
        restored = TimingEvidence.model_validate_json(original.model_dump_json())
        assert original.model_dump() == restored.model_dump()


# ===========================================================================
# 2. Invalid data
# ===========================================================================

class TestInvalidData:
    def test_negative_transaction_amount(self):
        with pytest.raises(ValidationError):
            _transaction(amount=-100)

    def test_zero_transaction_amount(self):
        with pytest.raises(ValidationError):
            _transaction(amount=0)

    def test_negative_settlement_amount(self):
        with pytest.raises(ValidationError):
            _settlement(amount=-100)

    def test_negative_refund_amount(self):
        with pytest.raises(ValidationError):
            _refund(amount=-100)

    def test_negative_bank_credit_amount(self):
        with pytest.raises(ValidationError):
            _bank_credit(amount=-100)

    def test_invalid_transaction_status(self):
        with pytest.raises(ValidationError):
            _transaction(status="unknown_status")

    def test_invalid_payment_method(self):
        with pytest.raises(ValidationError):
            _transaction(method="crypto")

    def test_invalid_settlement_status(self):
        with pytest.raises(ValidationError):
            _settlement(status="unknown_status")

    def test_invalid_refund_status(self):
        with pytest.raises(ValidationError):
            _refund(status="pending")

    def test_invalid_decision_state(self):
        with pytest.raises(ValidationError):
            _reconciliation_result(decision="UNKNOWN_STATE")

    def test_invalid_ai_classification(self):
        with pytest.raises(ValidationError):
            _ai_response(classification="UNKNOWN_CLASSIFICATION")

    def test_invalid_validation_result(self):
        with pytest.raises(ValidationError):
            _fees_summary(validation_result="maybe")

    def test_float_transaction_amount_strict(self):
        with pytest.raises(ValidationError):
            _transaction(amount=100.50)

    def test_float_settlement_amount_strict(self):
        with pytest.raises(ValidationError):
            _settlement(amount=100.50)

    def test_float_refund_amount_strict(self):
        with pytest.raises(ValidationError):
            _refund(amount=100.50)

    def test_float_bank_credit_amount_strict(self):
        with pytest.raises(ValidationError):
            _bank_credit(amount=100.50)

    def test_string_for_int_field(self):
        with pytest.raises(ValidationError):
            _transaction(amount="100000")

    def test_negative_fee(self):
        with pytest.raises(ValidationError):
            _transaction(fee=-10)

    def test_negative_tax(self):
        with pytest.raises(ValidationError):
            _transaction(tax=-10)

    def test_settled_at_before_created_at(self):
        with pytest.raises(ValidationError):
            _settlement(
                created_at=datetime(2026, 8, 21, 10, 0, 0),
                settled_at=datetime(2026, 8, 20, 10, 0, 0),
            )

    def test_negative_linked_payments_summary_count(self):
        with pytest.raises(ValidationError):
            _linked_payments_summary(count=-1)

    def test_negative_linked_refunds_summary_count(self):
        with pytest.raises(ValidationError):
            _linked_refunds_summary(count=-1)

    def test_negative_fees_summary_total(self):
        with pytest.raises(ValidationError):
            _fees_summary(total_paise=-100)

    def test_negative_tax_summary_total(self):
        with pytest.raises(ValidationError):
            _tax_summary(total_paise=-100)

    def test_negative_timing_expected_cycle_days(self):
        with pytest.raises(ValidationError):
            _timing_evidence(expected_cycle_days=-1)

    def test_bank_credit_evidence_zero_amount(self):
        with pytest.raises(ValidationError):
            _bank_credit_evidence(amount_paise=0)

    def test_negative_batch_metrics_count(self):
        with pytest.raises(ValidationError):
            _batch_metrics(total_settlements=-1)

    def test_negative_evaluation_result_rate(self):
        with pytest.raises(ValidationError):
            _evaluation_result(match_rate=-0.1)


# ===========================================================================
# 3. Post-construction mutation
# ===========================================================================

class TestPostConstructionMutation:
    def test_invalid_amount_mutation_transaction(self):
        t = _transaction()
        with pytest.raises(ValidationError):
            t.amount = -100

    def test_invalid_status_mutation_transaction(self):
        t = _transaction()
        with pytest.raises(ValidationError):
            t.status = "invalid"

    def test_invalid_amount_mutation_settlement(self):
        s = _settlement()
        with pytest.raises(ValidationError):
            s.amount = -100

    def test_invalid_amount_mutation_refund(self):
        r = _refund()
        with pytest.raises(ValidationError):
            r.amount = -100

    def test_invalid_amount_mutation_bank_credit(self):
        b = _bank_credit()
        with pytest.raises(ValidationError):
            b.amount = -100

    def test_invalid_difference_mutation_reconciliation(self):
        r = _reconciliation_result()
        with pytest.raises(ValidationError):
            r.difference_paise = 999

    def test_invalid_classification_mutation_ai_response(self):
        a = _ai_response()
        with pytest.raises(ValidationError):
            a.classification = "UNKNOWN"

    def test_invalid_confidence_mutation_ai_response(self):
        a = _ai_response()
        with pytest.raises(ValidationError):
            a.raw_confidence = 2.0

    def test_valid_mutation_transaction(self):
        t = _transaction()
        t.amount = 200000
        assert t.amount == 200000

    def test_valid_mutation_settlement(self):
        s = _settlement()
        s.amount = 200000
        assert s.amount == 200000


# ===========================================================================
# 4. AIResponse security
# ===========================================================================

class TestAIResponseSecurity:
    def test_rejects_expected_amount_field(self):
        with pytest.raises(ValidationError):
            AIResponse(
                classification=AIClassification.TIMING_MISMATCH,
                explanation="test",
                raw_confidence=0.5,
                cited_evidence=[],
                expected_amount=1000,
            )

    def test_rejects_actual_amount_paise_field(self):
        with pytest.raises(ValidationError):
            AIResponse(
                classification=AIClassification.TIMING_MISMATCH,
                explanation="test",
                raw_confidence=0.5,
                cited_evidence=[],
                actual_amount_paise=1000,
            )

    def test_rejects_difference_paise_field(self):
        with pytest.raises(ValidationError):
            AIResponse(
                classification=AIClassification.TIMING_MISMATCH,
                explanation="test",
                raw_confidence=0.5,
                cited_evidence=[],
                difference_paise=100,
            )

    def test_rejects_fees_field(self):
        with pytest.raises(ValidationError):
            AIResponse(
                classification=AIClassification.TIMING_MISMATCH,
                explanation="test",
                raw_confidence=0.5,
                cited_evidence=[],
                fees=50,
            )

    def test_rejects_refunds_field(self):
        with pytest.raises(ValidationError):
            AIResponse(
                classification=AIClassification.TIMING_MISMATCH,
                explanation="test",
                raw_confidence=0.5,
                cited_evidence=[],
                refunds=30,
            )

    def test_rejects_tax_field(self):
        with pytest.raises(ValidationError):
            AIResponse(
                classification=AIClassification.TIMING_MISMATCH,
                explanation="test",
                raw_confidence=0.5,
                cited_evidence=[],
                tax=10,
            )

    def test_rejects_extra_unknown_field(self):
        with pytest.raises(ValidationError):
            AIResponse(
                classification=AIClassification.TIMING_MISMATCH,
                explanation="test",
                raw_confidence=0.5,
                cited_evidence=[],
                some_random_field="oops",
            )

    def test_recommended_action_is_hardcoded(self):
        a = _ai_response()
        assert a.recommended_action == "ESCALATE_TO_HUMAN"


# ===========================================================================
# 5. EvidencePacket privacy
# ===========================================================================

class TestEvidencePacketPrivacy:
    def test_customer_email_not_in_schema(self):
        fields = EvidencePacket.model_fields
        assert "customer_email" not in fields

    def test_customer_name_not_in_schema(self):
        fields = EvidencePacket.model_fields
        assert "customer_name" not in fields

    def test_customer_phone_not_in_schema(self):
        fields = EvidencePacket.model_fields
        assert "customer_phone" not in fields

    def test_pii_not_in_model_dump(self):
        ep = _evidence_packet()
        dumped = ep.model_dump()
        assert "customer_email" not in dumped
        assert "customer_name" not in dumped
        assert "customer_phone" not in dumped

    def test_pii_not_in_json_round_trip(self):
        ep = _evidence_packet()
        json_str = ep.model_dump_json()
        assert "customer_email" not in json_str
        assert "customer_name" not in json_str
        assert "customer_phone" not in json_str


# ===========================================================================
# 6. Difference consistency
# ===========================================================================

class TestDifferenceConsistency:
    def test_valid_difference(self):
        """expected=1000, actual=800, difference=-200 (actual - expected)"""
        r = _reconciliation_result(
            decision=DecisionState.DETERMINISTIC_EXCEPTION,
            difference_paise=-200,
            expected_amount_paise=1000,
            actual_amount_paise=800,
            escalate_to_human=True,
        )
        assert r.difference_paise == -200

    def test_invalid_difference(self):
        """expected=1000, actual=800, difference=100 (wrong)"""
        with pytest.raises(ValidationError):
            _reconciliation_result(
                decision=DecisionState.DETERMINISTIC_EXCEPTION,
                difference_paise=100,
                expected_amount_paise=1000,
                actual_amount_paise=800,
                escalate_to_human=True,
            )

    def test_positive_difference(self):
        """actual > expected: actual=1200, expected=1000, difference=200"""
        r = _reconciliation_result(
            decision=DecisionState.DETERMINISTIC_EXCEPTION,
            difference_paise=200,
            expected_amount_paise=1000,
            actual_amount_paise=1200,
            escalate_to_human=True,
        )
        assert r.difference_paise == 200

    def test_zero_difference(self):
        r = _reconciliation_result(
            decision=DecisionState.CLEAN_MATCH,
            difference_paise=0,
            expected_amount_paise=1000,
            actual_amount_paise=1000,
        )
        assert r.difference_paise == 0

    def test_invalid_difference_post_mutation(self):
        r = _reconciliation_result(
            decision=DecisionState.DETERMINISTIC_EXCEPTION,
            difference_paise=-200,
            expected_amount_paise=1000,
            actual_amount_paise=800,
            escalate_to_human=True,
        )
        with pytest.raises(ValidationError):
            r.difference_paise = 100


# ===========================================================================
# 7. CLEAN_MATCH
# ===========================================================================

class TestCleanMatch:
    def test_clean_match_with_zero_difference(self):
        r = _reconciliation_result(
            decision=DecisionState.CLEAN_MATCH,
            difference_paise=0,
            expected_amount_paise=1000,
            actual_amount_paise=1000,
        )
        assert r.decision == DecisionState.CLEAN_MATCH
        assert r.difference_paise == 0

    def test_clean_match_with_nonzero_difference_rejected(self):
        with pytest.raises(ValidationError):
            _reconciliation_result(
                decision=DecisionState.CLEAN_MATCH,
                difference_paise=100,
                expected_amount_paise=1000,
                actual_amount_paise=1100,
            )

    def test_clean_match_with_negative_difference_rejected(self):
        with pytest.raises(ValidationError):
            _reconciliation_result(
                decision=DecisionState.CLEAN_MATCH,
                difference_paise=-100,
                expected_amount_paise=1000,
                actual_amount_paise=900,
            )

    def test_non_clean_match_with_nonzero_difference(self):
        r = _reconciliation_result(
            decision=DecisionState.DETERMINISTIC_EXCEPTION,
            difference_paise=-100,
            expected_amount_paise=1000,
            actual_amount_paise=900,
            escalate_to_human=True,
        )
        assert r.decision == DecisionState.DETERMINISTIC_EXCEPTION


# ===========================================================================
# 8. BatchMetrics
# ===========================================================================

class TestBatchMetrics:
    def test_valid_auto_approved_by_ai_zero(self):
        m = _batch_metrics(auto_approved_by_ai=0)
        assert m.auto_approved_by_ai == 0

    def test_invalid_auto_approved_by_ai_positive(self):
        with pytest.raises(ValidationError):
            _batch_metrics(auto_approved_by_ai=1)

    def test_invalid_auto_approved_by_ai_negative(self):
        with pytest.raises(ValidationError):
            _batch_metrics(auto_approved_by_ai=-1)

    def test_valid_mutation(self):
        m = _batch_metrics()
        m.total_settlements = 100
        assert m.total_settlements == 100

    def test_invalid_mutation_auto_approved(self):
        m = _batch_metrics()
        with pytest.raises(ValidationError):
            m.auto_approved_by_ai = 5


# ===========================================================================
# 9. EvaluationResult
# ===========================================================================

class TestEvaluationResult:
    def test_valid_ai_auto_approval_rate_zero(self):
        e = _evaluation_result(ai_auto_approval_rate_pct=0.0)
        assert e.ai_auto_approval_rate_pct == 0.0

    def test_invalid_ai_auto_approval_rate_positive(self):
        with pytest.raises(ValidationError):
            _evaluation_result(ai_auto_approval_rate_pct=5.0)

    def test_invalid_ai_auto_approval_rate_negative(self):
        with pytest.raises(ValidationError):
            _evaluation_result(ai_auto_approval_rate_pct=-1.0)

    def test_valid_mutation(self):
        e = _evaluation_result()
        e.match_rate = 0.9
        assert e.match_rate == 0.9

    def test_invalid_mutation_auto_approval_rate(self):
        e = _evaluation_result()
        with pytest.raises(ValidationError):
            e.ai_auto_approval_rate_pct = 10.0


# ===========================================================================
# 10. Assignment validation (validate_assignment=True)
# ===========================================================================

class TestAssignmentValidation:
    def test_linked_payments_summary_validate_assignment(self):
        m = _linked_payments_summary()
        with pytest.raises(ValidationError):
            m.count = -1

    def test_linked_refunds_summary_validate_assignment(self):
        m = _linked_refunds_summary()
        with pytest.raises(ValidationError):
            m.count = -1

    def test_fees_summary_validate_assignment(self):
        m = _fees_summary()
        with pytest.raises(ValidationError):
            m.total_paise = -100

    def test_tax_summary_validate_assignment(self):
        m = _tax_summary()
        with pytest.raises(ValidationError):
            m.total_paise = -100

    def test_bank_credit_evidence_validate_assignment(self):
        m = _bank_credit_evidence()
        with pytest.raises(ValidationError):
            m.amount_paise = -100

    def test_timing_evidence_validate_assignment(self):
        m = _timing_evidence()
        with pytest.raises(ValidationError):
            m.expected_cycle_days = -1

    def test_transaction_validate_assignment(self):
        t = _transaction()
        with pytest.raises(ValidationError):
            t.amount = -100

    def test_settlement_validate_assignment(self):
        s = _settlement()
        with pytest.raises(ValidationError):
            s.amount = -100

    def test_refund_validate_assignment(self):
        r = _refund()
        with pytest.raises(ValidationError):
            r.amount = -100

    def test_bank_credit_validate_assignment(self):
        b = _bank_credit()
        with pytest.raises(ValidationError):
            b.amount = -100

    def test_evidence_packet_validate_assignment(self):
        ep = _evidence_packet()
        with pytest.raises(ValidationError):
            ep.actual_amount_paise = -100

    def test_ai_response_validate_assignment(self):
        a = _ai_response()
        with pytest.raises(ValidationError):
            a.raw_confidence = -1.0

    def test_reconciliation_result_validate_assignment(self):
        r = _reconciliation_result()
        with pytest.raises(ValidationError):
            r.actual_amount_paise = -100

    def test_batch_metrics_validate_assignment(self):
        m = _batch_metrics()
        with pytest.raises(ValidationError):
            m.total_settlements = -1

    def test_evaluation_result_validate_assignment(self):
        e = _evaluation_result()
        with pytest.raises(ValidationError):
            e.match_rate = -1.0
