"""
Phase 4 Tests: Deterministic Reconciliation Engine

Tests all engine checks, outcomes, fee/tax formulas, edge cases.
"""

import math
import pytest
from unittest.mock import patch
from datetime import datetime, date

from backend.engine import (
    compute_fee,
    compute_tax,
    reconcile_settlement,
    run_engine,
    detect_duplicates,
    detect_cross_file_utr_duplicates,
)
from backend.models import DecisionState, PaymentMethod, ReconciliationResult


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


def _refund(rid, pid, amount):
    return {
        "refund_id": rid,
        "payment_id": pid,
        "amount": amount,
        "status": "processed",
        "created_at": datetime(2026, 8, 20, 12, 0, 0),
    }


def _bank_credit(utr="UTR_001", amount=100000):
    return {
        "utr": utr,
        "amount": amount,
        "date": date(2026, 8, 22),
        "description": "NEFT credit",
        "bank_account": "ACC_001",
    }


# ---------------------------------------------------------------------------
# Fee / tax formula tests
# ---------------------------------------------------------------------------

class TestComputeFee:
    def test_upi_fee_is_zero(self):
        assert compute_fee(PaymentMethod.UPI, 100000) == 0
        assert compute_fee(PaymentMethod.UPI, 1) == 0
        assert compute_fee(PaymentMethod.UPI, 999999) == 0

    def test_card_fee_formula(self):
        # floor(100000 * 0.02) + 100 = 2000 + 100 = 2100
        assert compute_fee(PaymentMethod.CARD, 100000) == 2100
        # floor(50000 * 0.02) + 100 = 1000 + 100 = 1100
        assert compute_fee(PaymentMethod.CARD, 50000) == 1100
        # floor(1 * 0.02) + 100 = 0 + 100 = 100
        assert compute_fee(PaymentMethod.CARD, 1) == 100
        # floor(9999 * 0.02) + 100 = 199 + 100 = 299
        assert compute_fee(PaymentMethod.CARD, 9999) == 299

    def test_netbanking_fee_formula(self):
        # floor(100000 * 0.015) + 100 = 1500 + 100 = 1600
        assert compute_fee(PaymentMethod.NETBANKING, 100000) == 1600
        # floor(50000 * 0.015) + 100 = 750 + 100 = 850
        assert compute_fee(PaymentMethod.NETBANKING, 50000) == 850
        # floor(1 * 0.015) + 100 = 0 + 100 = 100
        assert compute_fee(PaymentMethod.NETBANKING, 1) == 100
        # floor(6666 * 0.015) + 100 = 99 + 100 = 199
        assert compute_fee(PaymentMethod.NETBANKING, 6666) == 199

    def test_floor_truncation_card(self):
        # 10005 * 0.02 = 200.1 → floor = 200 → +100 = 300
        assert compute_fee(PaymentMethod.CARD, 10005) == 300

    def test_floor_truncation_netbanking(self):
        # 10003 * 0.015 = 150.045 → floor = 150 → +100 = 250
        assert compute_fee(PaymentMethod.NETBANKING, 10003) == 250


class TestComputeTax:
    def test_zero_fee(self):
        assert compute_tax(0) == 0

    def test_basic_tax(self):
        # floor(2100 * 0.18) = floor(378) = 378
        assert compute_tax(2100) == 378

    def test_floor_truncation(self):
        # floor(100 * 0.18) = floor(18) = 18
        assert compute_tax(100) == 18
        # floor(55 * 0.18) = floor(9.9) = 9
        assert compute_tax(55) == 9
        # floor(1 * 0.18) = floor(0.18) = 0
        assert compute_tax(1) == 0

    def test_larger_amount(self):
        # floor(1600 * 0.18) = floor(288) = 288
        assert compute_tax(1600) == 288


# ---------------------------------------------------------------------------
# CLEAN_MATCH
# ---------------------------------------------------------------------------

class TestCleanMatch:
    def test_upi_clean_match(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100000)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.CLEAN_MATCH
        assert result.difference_paise == 0
        assert result.expected_amount_paise == 100000
        assert result.actual_amount_paise == 100000
        assert result.escalate_to_human is False

    def test_card_clean_match(self):
        fee = compute_fee(PaymentMethod.CARD, 100000)  # 2100
        tax = compute_tax(fee)  # 378
        expected = 100000 - 0 - fee - tax  # 97522
        t = _txn("PAY_001", amount=100000, method="card", fee=fee, tax=tax)
        s = _settlement("SETL_001", amount=expected, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", expected)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.CLEAN_MATCH
        assert result.difference_paise == 0
        assert result.expected_amount_paise == expected

    def test_netbanking_clean_match(self):
        fee = compute_fee(PaymentMethod.NETBANKING, 100000)  # 1600
        tax = compute_tax(fee)  # 288
        expected = 100000 - 0 - fee - tax  # 98112
        t = _txn("PAY_001", amount=100000, method="netbanking", fee=fee, tax=tax)
        s = _settlement("SETL_001", amount=expected, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", expected)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.CLEAN_MATCH
        assert result.difference_paise == 0

    def test_clean_match_with_refunds(self):
        fee = compute_fee(PaymentMethod.CARD, 100000)  # 2100
        tax = compute_tax(fee)  # 378
        refund_amount = 20000
        expected = 100000 - refund_amount - fee - tax  # 77522
        t = _txn("PAY_001", amount=100000, method="card", fee=fee, tax=tax)
        r = _refund("REF_001", "PAY_001", refund_amount)
        s = _settlement("SETL_001", amount=expected, linked_pids=["PAY_001"], linked_rids=["REF_001"])
        bc = _bank_credit("UTR_001", expected)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[r],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.CLEAN_MATCH
        assert result.difference_paise == 0

    def test_clean_match_multiple_payments(self):
        fee1 = compute_fee(PaymentMethod.CARD, 100000)  # 2100
        tax1 = compute_tax(fee1)  # 378
        fee2 = compute_fee(PaymentMethod.UPI, 50000)  # 0
        tax2 = compute_tax(fee2)  # 0
        total_fees = fee1 + fee2
        total_tax = tax1 + tax2
        expected = 100000 + 50000 - 0 - total_fees - total_tax

        t1 = _txn("PAY_001", amount=100000, method="card", fee=fee1, tax=tax1)
        t2 = _txn("PAY_002", amount=50000, method="upi", fee=fee2, tax=tax2)
        s = _settlement("SETL_001", amount=expected, linked_pids=["PAY_001", "PAY_002"])
        bc = _bank_credit("UTR_001", expected)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t1, t2],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.CLEAN_MATCH
        assert result.difference_paise == 0

    def test_clean_match_all_checks_passed(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100000)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert "schema_validation" in result.deterministic_checks_passed
        assert "duplicate_detection" in result.deterministic_checks_passed
        assert "reference_existence" in result.deterministic_checks_passed
        assert "linkage_consistency" in result.deterministic_checks_passed
        assert "fee_validation" in result.deterministic_checks_passed
        assert "tax_validation" in result.deterministic_checks_passed
        assert "bank_credit_existence" in result.deterministic_checks_passed
        assert "utr_cross_check" in result.deterministic_checks_passed
        assert "amount_cross_check" in result.deterministic_checks_passed
        assert "expected_amount_calculation" in result.deterministic_checks_passed
        assert "difference_calculation" in result.deterministic_checks_passed
        assert len(result.deterministic_checks_failed) == 0


# ---------------------------------------------------------------------------
# FEE_MISMATCH
# ---------------------------------------------------------------------------

class TestFeeMismatch:
    def test_fee_too_high(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=1, tax=0)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100000)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.DETERMINISTIC_EXCEPTION
        assert "fee_validation" in result.deterministic_checks_failed
        assert result.escalate_to_human is True

    def test_fee_too_low(self):
        t = _txn("PAY_001", amount=100000, method="card", fee=100, tax=18)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100000)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.DETERMINISTIC_EXCEPTION
        assert "fee_validation" in result.deterministic_checks_failed

    def test_fee_mismatch_stops_early(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=5, tax=0)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100000)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert "fee_validation" in result.deterministic_checks_failed
        assert "tax_validation" not in result.deterministic_checks_passed
        assert "tax_validation" not in result.deterministic_checks_failed


# ---------------------------------------------------------------------------
# TAX_INCONSISTENCY
# ---------------------------------------------------------------------------

class TestTaxInconsistency:
    def test_tax_too_high(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=1)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100000)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.DETERMINISTIC_EXCEPTION
        assert "tax_validation" in result.deterministic_checks_failed
        assert result.escalate_to_human is True

    def test_tax_too_low(self):
        fee = compute_fee(PaymentMethod.CARD, 100000)  # 2100
        correct_tax = compute_tax(fee)  # 378
        t = _txn("PAY_001", amount=100000, method="card", fee=fee, tax=correct_tax - 1)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100000)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.DETERMINISTIC_EXCEPTION
        assert "tax_validation" in result.deterministic_checks_failed

    def test_tax_mismatch_stops_early(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=5)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100000)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert "tax_validation" in result.deterministic_checks_failed
        assert "bank_credit_existence" not in result.deterministic_checks_passed
        assert "bank_credit_existence" not in result.deterministic_checks_failed


# ---------------------------------------------------------------------------
# BANK_MISMATCH
# ---------------------------------------------------------------------------

class TestBankMismatch:
    def test_no_bank_credit(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=None,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.DETERMINISTIC_EXCEPTION
        assert "bank_credit_existence" in result.deterministic_checks_failed
        assert result.escalate_to_human is True

    def test_bank_credit_wrong_amount(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 99999)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.DETERMINISTIC_EXCEPTION
        assert "amount_cross_check" in result.deterministic_checks_failed


# ---------------------------------------------------------------------------
# MATH_DISCREPANCY
# ---------------------------------------------------------------------------

class TestMathDiscrepancy:
    def test_actual_less_than_expected(self):
        fee = compute_fee(PaymentMethod.CARD, 100000)  # 2100
        tax = compute_tax(fee)  # 378
        expected = 100000 - fee - tax  # 97522
        actual = 97000  # 522 less than expected

        t = _txn("PAY_001", amount=100000, method="card", fee=fee, tax=tax)
        s = _settlement("SETL_001", amount=actual, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", actual)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.MATH_DISCREPANCY
        assert result.difference_paise == actual - expected
        assert result.expected_amount_paise == expected
        assert result.actual_amount_paise == actual
        assert result.escalate_to_human is True
        assert len(result.deterministic_checks_failed) == 0

    def test_actual_greater_than_expected(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        actual = 100500  # 500 more than expected
        s = _settlement("SETL_001", amount=actual, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", actual)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.MATH_DISCREPANCY
        assert result.difference_paise == 500
        assert len(result.deterministic_checks_failed) == 0

    def test_with_refunds_math_discrepancy(self):
        fee = compute_fee(PaymentMethod.CARD, 100000)  # 2100
        tax = compute_tax(fee)  # 378
        refund = 20000
        expected = 100000 - refund - fee - tax  # 77522
        actual = 77000

        t = _txn("PAY_001", amount=100000, method="card", fee=fee, tax=tax)
        r = _refund("REF_001", "PAY_001", refund)
        s = _settlement("SETL_001", amount=actual, linked_pids=["PAY_001"], linked_rids=["REF_001"])
        bc = _bank_credit("UTR_001", actual)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[r],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.MATH_DISCREPANCY
        assert result.difference_paise == actual - expected
        assert result.expected_amount_paise == expected


# ---------------------------------------------------------------------------
# UNPROCESSED (engine crash)
# ---------------------------------------------------------------------------

class TestUnprocessed:
    def test_engine_crash_returns_unprocessed(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100000)

        with patch("backend.engine.reconcile_settlement", side_effect=RuntimeError("boom")):
            results = run_engine([t], [s], [], [bc])

        assert len(results) == 1
        assert results[0].decision == DecisionState.UNPROCESSED
        assert results[0].escalate_to_human is True

    def test_missing_linkage_result_returns_unprocessed(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100000)

        with patch("backend.linking.link_entities", return_value=[]):
            results = run_engine([t], [s], [], [bc])

        assert len(results) == 1
        assert results[0].decision == DecisionState.UNPROCESSED
        assert results[0].escalate_to_human is True


# ---------------------------------------------------------------------------
# Linkage error outcomes
# ---------------------------------------------------------------------------

class TestLinkageErrors:
    def test_missing_reference(self):
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_MISSING"])
        bc = _bank_credit("UTR_001", 100000)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[{
                "error_type": "MISSING_REFERENCE",
                "entity_id": "PAY_MISSING",
                "message": "not found",
            }],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.DETERMINISTIC_EXCEPTION
        assert "reference_existence" in result.deterministic_checks_failed

    def test_payment_overclaim(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100000)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[{
                "error_type": "PAYMENT_OVERCLAIM",
                "entity_id": "PAY_001",
                "message": "in multiple settlements",
            }],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.DETERMINISTIC_EXCEPTION
        assert "linkage_consistency" in result.deterministic_checks_failed

    def test_refund_overage(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100000)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[{
                "error_type": "REFUND_OVERAGE",
                "entity_id": "PAY_001",
                "message": "refunds exceed payment",
            }],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.DETERMINISTIC_EXCEPTION
        assert "linkage_consistency" in result.deterministic_checks_failed


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection:
    def test_duplicate_payment_id(self):
        t1 = _txn("PAY_001", amount=50000, method="upi", fee=0, tax=0)
        t2 = _txn("PAY_001", amount=50000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001", "PAY_001"])
        bc = _bank_credit("UTR_001", 100000)

        dupes = detect_duplicates([t1, t2], "payment_id", "DUPLICATE_PAYMENT")
        assert len(dupes) == 1
        assert dupes[0]["error_type"] == "DUPLICATE_PAYMENT"

    def test_duplicate_settlement_id(self):
        s1 = _settlement("SETL_001", amount=50000, linked_pids=["PAY_001"])
        s2 = _settlement("SETL_001", amount=50000, linked_pids=["PAY_002"])
        dupes = detect_duplicates([s1, s2], "settlement_id", "DUPLICATE_SETTLEMENT")
        assert len(dupes) == 1
        assert dupes[0]["error_type"] == "DUPLICATE_SETTLEMENT"

    def test_duplicate_utr_in_settlements(self):
        s1 = _settlement("SETL_001", amount=50000, utr="UTR_ABC", linked_pids=["PAY_001"])
        s2 = _settlement("SETL_002", amount=50000, utr="UTR_ABC", linked_pids=["PAY_002"])
        errors = detect_cross_file_utr_duplicates([s1, s2], [])
        assert len(errors) == 1
        assert errors[0]["error_type"] == "DUPLICATE_UTR"

    def test_duplicate_utr_in_bank_credits(self):
        bc1 = _bank_credit("UTR_ABC", 50000)
        bc2 = _bank_credit("UTR_ABC", 50000)
        errors = detect_cross_file_utr_duplicates([], [bc1, bc2])
        assert len(errors) == 1
        assert errors[0]["error_type"] == "DUPLICATE_BANK_UTR"

    def test_no_duplicates(self):
        t1 = _txn("PAY_001", amount=50000, method="upi", fee=0, tax=0)
        t2 = _txn("PAY_002", amount=50000, method="upi", fee=0, tax=0)
        dupes = detect_duplicates([t1, t2], "payment_id", "DUPLICATE_PAYMENT")
        assert len(dupes) == 0


# ---------------------------------------------------------------------------
# Batch engine
# ---------------------------------------------------------------------------

class TestBatchEngine:
    def test_multiple_settlements(self):
        fee = compute_fee(PaymentMethod.CARD, 100000)
        tax = compute_tax(fee)
        expected = 100000 - fee - tax

        t1 = _txn("PAY_001", amount=100000, method="card", fee=fee, tax=tax)
        t2 = _txn("PAY_002", amount=50000, method="upi", fee=0, tax=0)
        s1 = _settlement("SETL_001", amount=expected, linked_pids=["PAY_001"])
        s2 = _settlement("SETL_002", amount=50000, utr="UTR_002", linked_pids=["PAY_002"])
        bc1 = _bank_credit("UTR_001", expected)
        bc2 = _bank_credit("UTR_002", 50000)

        results = run_engine([t1, t2], [s1, s2], [], [bc1, bc2])

        assert len(results) == 2
        assert results[0].decision == DecisionState.CLEAN_MATCH
        assert results[1].decision == DecisionState.CLEAN_MATCH

    def test_mixed_outcomes(self):
        t1 = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        t2 = _txn("PAY_002", amount=100000, method="upi", fee=0, tax=1)
        s1 = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])
        s2 = _settlement("SETL_002", amount=100000, utr="UTR_002", linked_pids=["PAY_002"])
        bc1 = _bank_credit("UTR_001", 100000)
        bc2 = _bank_credit("UTR_002", 100000)

        results = run_engine([t1, t2], [s1, s2], [], [bc1, bc2])

        assert len(results) == 2
        assert results[0].decision == DecisionState.CLEAN_MATCH
        # DETERMINISTIC_EXCEPTION gets AI investigation → REVIEW_REQUIRED
        assert results[1].decision == DecisionState.REVIEW_REQUIRED
        assert "tax_validation" in results[1].deterministic_checks_failed
        assert results[1].ai_response is not None

    def test_empty_batch(self):
        results = run_engine([], [], [], [])
        assert results == []


# ---------------------------------------------------------------------------
# ReconciliationResult model validation
# ---------------------------------------------------------------------------

class TestReconciliationResultValidation:
    def test_clean_match_zero_difference(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100000)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        # ReconciliationResult validators should pass
        assert result.decision == DecisionState.CLEAN_MATCH
        assert result.difference_paise == 0

    def test_difference_consistency(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100500, linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100500)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        # difference_paise should equal actual - expected
        assert result.difference_paise == 100500 - 100000
        assert result.expected_amount_paise == 100000
        assert result.actual_amount_paise == 100500


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_zero_refunds(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100000, linked_pids=["PAY_001"], linked_rids=[])
        bc = _bank_credit("UTR_001", 100000)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.CLEAN_MATCH

    def test_empty_linked_payments(self):
        s = _settlement("SETL_001", amount=50000, linked_pids=[], linked_rids=[])
        bc = _bank_credit("UTR_001", 50000)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.MATH_DISCREPANCY
        assert result.expected_amount_paise == 0
        assert result.difference_paise == 50000

    def test_multiple_refunds(self):
        fee = compute_fee(PaymentMethod.CARD, 200000)  # 4100
        tax = compute_tax(fee)  # 738
        r1_amount = 30000
        r2_amount = 20000
        expected = 200000 - r1_amount - r2_amount - fee - tax

        t = _txn("PAY_001", amount=200000, method="card", fee=fee, tax=tax)
        r1 = _refund("REF_001", "PAY_001", r1_amount)
        r2 = _refund("REF_002", "PAY_001", r2_amount)
        s = _settlement("SETL_001", amount=expected, linked_pids=["PAY_001"], linked_rids=["REF_001", "REF_002"])
        bc = _bank_credit("UTR_001", expected)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[r1, r2],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.CLEAN_MATCH
        assert result.expected_amount_paise == expected

    def test_utr_mismatch_with_bank_credit(self):
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100000, utr="UTR_SETTLE", linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_BANK", 100000)

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=[],
        )

        assert result.decision == DecisionState.DETERMINISTIC_EXCEPTION
        assert "utr_cross_check" in result.deterministic_checks_failed


# ---------------------------------------------------------------------------
# DUPLICATE_BANK_UTR safety regression
# ---------------------------------------------------------------------------

class TestDuplicateBankUTRSafety:
    """Duplicate bank UTR must never result in CLEAN_MATCH."""

    def test_duplicate_bank_utr_with_matching_settlement_utr(self):
        """Settlement UTR matches the duplicated bank UTR → must be caught."""
        t1 = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        t2 = _txn("PAY_002", amount=100000, method="upi", fee=0, tax=0)
        s1 = _settlement("SETL_001", amount=100000, utr="UTR_DUP", linked_pids=["PAY_001"])
        s2 = _settlement("SETL_002", amount=100000, utr="UTR_DUP", linked_pids=["PAY_002"])
        bc1 = _bank_credit("UTR_DUP", 100000)
        bc2 = _bank_credit("UTR_DUP", 100000)

        results = run_engine([t1, t2], [s1, s2], [], [bc1, bc2])

        for r in results:
            assert r.decision != DecisionState.CLEAN_MATCH, (
                f"Settlement {r.settlement_id} must not be CLEAN_MATCH with duplicate bank UTR"
            )

    def test_duplicate_bank_utr_with_different_settlement_utr(self):
        """Settlement UTR differs from duplicated bank UTR → must still be caught via linked_bank_utr."""
        t1 = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        t2 = _txn("PAY_002", amount=100000, method="upi", fee=0, tax=0)
        s1 = _settlement("SETL_001", amount=100000, utr="UTR_S1", linked_pids=["PAY_001"])
        s2 = _settlement("SETL_002", amount=100000, utr="UTR_S2", linked_pids=["PAY_002"])
        bc1 = _bank_credit("UTR_DUP", 100000)
        bc2 = _bank_credit("UTR_DUP", 100000)

        results = run_engine([t1, t2], [s1, s2], [], [bc1, bc2])

        for r in results:
            assert r.decision != DecisionState.CLEAN_MATCH, (
                f"Settlement {r.settlement_id} must not be CLEAN_MATCH with duplicate bank UTR"
            )

    def test_duplicate_bank_utr_perfect_math(self):
        """Even with mathematically perfect settlement, duplicate bank UTR blocks CLEAN_MATCH.
        
        Tests the reconcile_settlement function directly with linked_bank_utr parameter,
        which is the core safety mechanism. The run_engine integration test uses distinct
        UTRs for linking clarity.
        """
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100000, utr="UTR_S1", linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_DUP", 100000)

        duplicate_errors = [{
            "error_type": "DUPLICATE_BANK_UTR",
            "entity_id": "UTR_DUP",
            "message": "Duplicate UTR in bank_credits: UTR_DUP",
        }]

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=duplicate_errors,
            linked_bank_utr="UTR_DUP",
        )

        assert result.decision == DecisionState.DETERMINISTIC_EXCEPTION
        assert "duplicate_detection" in result.deterministic_checks_failed

    def test_no_duplicate_bank_utr_allows_clean(self):
        """Without duplicate bank UTR, a perfect settlement can be CLEAN_MATCH."""
        t1 = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s1 = _settlement("SETL_001", amount=100000, utr="UTR_S1", linked_pids=["PAY_001"])
        bc1 = _bank_credit("UTR_S1", 100000)

        results = run_engine([t1], [s1], [], [bc1])

        assert len(results) == 1
        assert results[0].decision == DecisionState.CLEAN_MATCH

    def test_linked_bank_utr_parameter_catches_duplicate(self):
        """Direct test of reconcile_settlement with linked_bank_utr parameter."""
        t = _txn("PAY_001", amount=100000, method="upi", fee=0, tax=0)
        s = _settlement("SETL_001", amount=100000, utr="UTR_S1", linked_pids=["PAY_001"])
        bc = _bank_credit("UTR_DUP", 100000)

        duplicate_errors = [{
            "error_type": "DUPLICATE_BANK_UTR",
            "entity_id": "UTR_DUP",
            "message": "Duplicate UTR in bank_credits: UTR_DUP",
        }]

        result = reconcile_settlement(
            settlement=s,
            linked_payments=[t],
            linked_refunds=[],
            bank_credit=bc,
            linkage_errors=[],
            duplicate_errors=duplicate_errors,
            linked_bank_utr="UTR_DUP",
        )

        assert result.decision == DecisionState.DETERMINISTIC_EXCEPTION
        assert "duplicate_detection" in result.deterministic_checks_failed
