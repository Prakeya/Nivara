"""Tests for Phase 3: Entity Linking."""

import pytest
from datetime import datetime, date

from backend.linking import (
    LinkageError,
    LinkageResult,
    build_payment_index,
    build_refund_index,
    build_refunds_by_payment_index,
    detect_payment_overclaims,
    detect_refund_overages,
    detect_cross_check_mismatches,
    link_bank_credit,
    link_settlement,
    link_entities,
)


# ---------------------------------------------------------------------------
# Fixtures: minimal valid records
# ---------------------------------------------------------------------------

def _txn(pid="PAY_001", amount=100000, settlement_id=None):
    return {
        "payment_id": pid,
        "order_id": "ORD_001",
        "amount": amount,
        "status": "captured",
        "method": "upi",
        "fee": 0,
        "tax": 0,
        "created_at": datetime(2026, 8, 20, 10, 0, 0),
        "settlement_id": settlement_id,
    }


def _settlement(sid="SETL_001", amount=100000, utr="UTR_001",
                linked_payment_ids=None, linked_refund_ids=None,
                created_at=None, settled_at=None):
    return {
        "settlement_id": sid,
        "amount": amount,
        "status": "settled",
        "utr": utr,
        "created_at": created_at or datetime(2026, 8, 20, 10, 0, 0),
        "settled_at": settled_at or datetime(2026, 8, 21, 8, 0, 0),
        "linked_payment_ids": linked_payment_ids or ["PAY_001"],
        "linked_refund_ids": linked_refund_ids or [],
    }


def _refund(rid="REF_001", pid="PAY_001", amount=5000):
    return {
        "refund_id": rid,
        "payment_id": pid,
        "amount": amount,
        "status": "processed",
        "created_at": datetime(2026, 8, 20, 12, 0, 0),
    }


def _bank_credit(utr="UTR_001", amount=100000, bc_date=None):
    return {
        "utr": utr,
        "amount": amount,
        "date": bc_date or date(2026, 8, 22),
        "description": "NEFT credit",
        "bank_account": "ACC_001",
    }


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------

class TestBuildPaymentIndex:
    def test_builds_index(self):
        t1 = _txn("PAY_001")
        t2 = _txn("PAY_002")
        idx = build_payment_index([t1, t2])
        assert idx["PAY_001"] == t1
        assert idx["PAY_002"] == t2

    def test_empty_list(self):
        assert build_payment_index([]) == {}


class TestBuildRefundIndex:
    def test_builds_index(self):
        r1 = _refund("REF_001")
        r2 = _refund("REF_002")
        idx = build_refund_index([r1, r2])
        assert idx["REF_001"] == r1
        assert idx["REF_002"] == r2


class TestBuildRefundsByPaymentIndex:
    def test_groups_by_payment(self):
        r1 = _refund("REF_001", "PAY_001", 5000)
        r2 = _refund("REF_002", "PAY_001", 3000)
        r3 = _refund("REF_003", "PAY_002", 2000)
        idx = build_refunds_by_payment_index([r1, r2, r3])
        assert len(idx["PAY_001"]) == 2
        assert len(idx["PAY_002"]) == 1


# ---------------------------------------------------------------------------
# PAYMENT_OVERCLAIM detection
# ---------------------------------------------------------------------------

class TestDetectPaymentOverclaims:
    def test_no_overclaim(self):
        s1 = _settlement("SETL_001", linked_payment_ids=["PAY_001"])
        s2 = _settlement("SETL_002", linked_payment_ids=["PAY_002"])
        errors = detect_payment_overclaims([s1, s2])
        assert errors == []

    def test_overclaim_detected(self):
        s1 = _settlement("SETL_001", linked_payment_ids=["PAY_001", "PAY_002"])
        s2 = _settlement("SETL_002", linked_payment_ids=["PAY_002", "PAY_003"])
        errors = detect_payment_overclaims([s1, s2])
        assert len(errors) == 1
        assert errors[0]["error_type"] == LinkageError.PAYMENT_OVERCLAIM.value
        assert "PAY_002" in errors[0]["entity_id"]

    def test_empty_settlements(self):
        assert detect_payment_overclaims([]) == []


# ---------------------------------------------------------------------------
# REFUND_OVERAGE detection
# ---------------------------------------------------------------------------

class TestDetectRefundOverages:
    def test_no_overage(self):
        t = _txn("PAY_001", amount=100000)
        r = _refund("REF_001", "PAY_001", 50000)
        errors = detect_refund_overages([t], [r])
        assert errors == []

    def test_overage_detected(self):
        t = _txn("PAY_001", amount=100000)
        r1 = _refund("REF_001", "PAY_001", 60000)
        r2 = _refund("REF_002", "PAY_001", 50000)
        errors = detect_refund_overages([t], [r1, r2])
        assert len(errors) == 1
        assert errors[0]["error_type"] == LinkageError.REFUND_OVERAGE.value

    def test_exact_amount_no_error(self):
        t = _txn("PAY_001", amount=100000)
        r = _refund("REF_001", "PAY_001", 100000)
        errors = detect_refund_overages([t], [r])
        assert errors == []


# ---------------------------------------------------------------------------
# LINKAGE_MISMATCH / ORPHAN_PAYMENT detection
# ---------------------------------------------------------------------------

class TestDetectCrossCheckMismatches:
    def test_no_mismatch(self):
        t = _txn("PAY_001", settlement_id="SETL_001")
        s = _settlement("SETL_001", linked_payment_ids=["PAY_001"])
        errors = detect_cross_check_mismatches([t], [s])
        assert errors == []

    def test_linkage_mismatch(self):
        t = _txn("PAY_001", settlement_id="SETL_001")
        s = _settlement("SETL_001", linked_payment_ids=["PAY_002"])
        errors = detect_cross_check_mismatches([t], [s])
        assert len(errors) == 1
        assert errors[0]["error_type"] == LinkageError.LINKAGE_MISMATCH.value

    def test_orphan_payment(self):
        t = _txn("PAY_001", settlement_id="SETL_999")
        s = _settlement("SETL_001", linked_payment_ids=["PAY_001"])
        errors = detect_cross_check_mismatches([t], [s])
        assert len(errors) == 1
        assert errors[0]["error_type"] == LinkageError.ORPHAN_PAYMENT.value

    def test_transaction_without_settlement_id(self):
        t = _txn("PAY_001", settlement_id=None)
        s = _settlement("SETL_001", linked_payment_ids=["PAY_001"])
        errors = detect_cross_check_mismatches([t], [s])
        assert errors == []


# ---------------------------------------------------------------------------
# Bank credit linking
# ---------------------------------------------------------------------------

class TestLinkBankCredit:
    def test_primary_utr_match(self):
        s = _settlement("SETL_001", amount=100000, utr="UTR_ABC",
                        settled_at=datetime(2026, 8, 21, 8, 0, 0))
        bc = _bank_credit("UTR_ABC", 100000, date(2026, 8, 22))
        result = link_bank_credit(s, [bc])
        assert result == bc

    def test_primary_utr_match_wrong_amount(self):
        s = _settlement("SETL_001", amount=100000, utr="UTR_ABC",
                        settled_at=datetime(2026, 8, 21, 8, 0, 0))
        bc = _bank_credit("UTR_ABC", 99999, date(2026, 8, 22))
        result = link_bank_credit(s, [bc])
        assert result is None

    def test_primary_utr_match_date_out_of_range(self):
        s = _settlement("SETL_001", amount=100000, utr="UTR_ABC",
                        settled_at=datetime(2026, 8, 21, 8, 0, 0))
        bc = _bank_credit("UTR_ABC", 100000, date(2026, 8, 25))
        result = link_bank_credit(s, [bc])
        assert result is None

    def test_fallback_no_utr_amount_date_match(self):
        s = _settlement("SETL_001", amount=100000, utr="UTR_ABC",
                        settled_at=datetime(2026, 8, 21, 8, 0, 0))
        bc = _bank_credit(None, 100000, date(2026, 8, 22))
        result = link_bank_credit(s, [bc])
        assert result == bc

    def test_fallback_no_utr_date_out_of_range(self):
        s = _settlement("SETL_001", amount=100000, utr="UTR_ABC",
                        settled_at=datetime(2026, 8, 21, 8, 0, 0))
        bc = _bank_credit(None, 100000, date(2026, 8, 30))
        result = link_bank_credit(s, [bc])
        assert result is None

    def test_no_matching_bank_credit(self):
        s = _settlement("SETL_001", amount=100000, utr="UTR_ABC",
                        settled_at=datetime(2026, 8, 21, 8, 0, 0))
        bc = _bank_credit("UTR_DIFF", 100000, date(2026, 8, 22))
        result = link_bank_credit(s, [bc])
        assert result is None

    def test_empty_bank_credits(self):
        s = _settlement("SETL_001", amount=100000, utr="UTR_ABC")
        result = link_bank_credit(s, [])
        assert result is None


# ---------------------------------------------------------------------------
# Single settlement linking
# ---------------------------------------------------------------------------

class TestLinkSettlement:
    def test_happy_path(self):
        t = _txn("PAY_001", amount=100000)
        s = _settlement("SETL_001", amount=100000, utr="UTR_001",
                        linked_payment_ids=["PAY_001"], linked_refund_ids=[])
        bc = _bank_credit("UTR_001", 100000, date(2026, 8, 22))
        payment_idx = build_payment_index([t])
        refund_idx = build_refund_index([])
        refunds_by_pid = build_refunds_by_payment_index([])

        lr = link_settlement(s, payment_idx, refund_idx, refunds_by_pid, [bc])
        assert len(lr.linked_payments) == 1
        assert lr.linked_payments[0]["payment_id"] == "PAY_001"
        assert lr.bank_credit is not None
        assert not lr.has_errors

    def test_missing_payment_reference(self):
        s = _settlement("SETL_001", linked_payment_ids=["PAY_MISSING"])
        payment_idx = build_payment_index([])
        refund_idx = build_refund_index([])
        refunds_by_pid = build_refunds_by_payment_index([])

        lr = link_settlement(s, payment_idx, refund_idx, refunds_by_pid, [])
        assert len(lr.linked_payments) == 0
        assert lr.has_errors
        assert any(e["error_type"] == LinkageError.MISSING_REFERENCE.value for e in lr.errors)

    def test_missing_refund_reference(self):
        r = _refund("REF_001", "PAY_001")
        s = _settlement("SETL_001", linked_refund_ids=["REF_MISSING"])
        payment_idx = build_payment_index([])
        refund_idx = build_refund_index([r])
        refunds_by_pid = build_refunds_by_payment_index([r])

        lr = link_settlement(s, payment_idx, refund_idx, refunds_by_pid, [])
        assert any(e["error_type"] == LinkageError.MISSING_REFERENCE.value for e in lr.errors)

    def test_bank_mismatch(self):
        s = _settlement("SETL_001", amount=100000, utr="UTR_001",
                        settled_at=datetime(2026, 8, 21, 8, 0, 0))
        payment_idx = build_payment_index([])
        refund_idx = build_refund_index([])
        refunds_by_pid = build_refunds_by_payment_index([])

        lr = link_settlement(s, payment_idx, refund_idx, refunds_by_pid, [])
        assert lr.bank_credit is None
        assert any(e["error_type"] == LinkageError.BANK_MISMATCH.value for e in lr.errors)


# ---------------------------------------------------------------------------
# Full entity linking pipeline
# ---------------------------------------------------------------------------

class TestLinkEntities:
    def test_happy_path_single_settlement(self):
        t = _txn("PAY_001", amount=100000)
        s = _settlement("SETL_001", amount=100000, utr="UTR_001",
                        linked_payment_ids=["PAY_001"])
        bc = _bank_credit("UTR_001", 100000, date(2026, 8, 22))

        results = link_entities([t], [s], [], [bc])
        assert len(results) == 1
        assert results[0].linked_payments[0]["payment_id"] == "PAY_001"
        assert results[0].bank_credit is not None
        assert not results[0].has_errors

    def test_multiple_settlements(self):
        t1 = _txn("PAY_001", amount=100000)
        t2 = _txn("PAY_002", amount=200000)
        s1 = _settlement("SETL_001", amount=100000, utr="UTR_001",
                         linked_payment_ids=["PAY_001"])
        s2 = _settlement("SETL_002", amount=200000, utr="UTR_002",
                         linked_payment_ids=["PAY_002"])
        bc1 = _bank_credit("UTR_001", 100000, date(2026, 8, 22))
        bc2 = _bank_credit("UTR_002", 200000, date(2026, 8, 22))

        results = link_entities([t1, t2], [s1, s2], [], [bc1, bc2])
        assert len(results) == 2
        assert all(not r.has_errors for r in results)

    def test_overclaim_detected_in_results(self):
        t1 = _txn("PAY_001", amount=100000)
        t2 = _txn("PAY_002", amount=200000)
        s1 = _settlement("SETL_001", linked_payment_ids=["PAY_001", "PAY_002"])
        s2 = _settlement("SETL_002", linked_payment_ids=["PAY_002"])

        results = link_entities([t1, t2], [s1, s2], [], [])
        overclaim_results = [r for r in results if any(
            e["error_type"] == LinkageError.PAYMENT_OVERCLAIM.value for e in r.errors
        )]
        assert len(overclaim_results) > 0

    def test_refund_overage_detected_in_results(self):
        t = _txn("PAY_001", amount=100000)
        r1 = _refund("REF_001", "PAY_001", 60000)
        r2 = _refund("REF_002", "PAY_001", 50000)
        s = _settlement("SETL_001", linked_payment_ids=["PAY_001"],
                        linked_refund_ids=["REF_001", "REF_002"])

        results = link_entities([t], [s], [r1, r2], [])
        overage_results = [r for r in results if any(
            e["error_type"] == LinkageError.REFUND_OVERAGE.value for e in r.errors
        )]
        assert len(overage_results) > 0

    def test_linkage_mismatch_detected_in_results(self):
        t = _txn("PAY_001", settlement_id="SETL_001")
        s = _settlement("SETL_001", linked_payment_ids=["PAY_002"])

        results = link_entities([t], [s], [], [])
        mismatch_results = [r for r in results if any(
            e["error_type"] == LinkageError.LINKAGE_MISMATCH.value for e in r.errors
        )]
        assert len(mismatch_results) > 0

    def test_orphan_payment_detected_in_results(self):
        t = _txn("PAY_001", settlement_id="SETL_999")
        s = _settlement("SETL_001", linked_payment_ids=["PAY_001"])

        results = link_entities([t], [s], [], [])
        orphan_results = [r for r in results if any(
            e["error_type"] == LinkageError.ORPHAN_PAYMENT.value for e in r.errors
        )]
        assert len(orphan_results) > 0

    def test_empty_inputs(self):
        results = link_entities([], [], [], [])
        assert results == []
