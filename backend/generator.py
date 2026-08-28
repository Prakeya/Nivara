"""
Phase 5: Synthetic Data Generator

Generates realistic settlement data with known ground-truth labels for
evaluation. Produces 4 CSV files (transactions, settlements, refunds,
bank_credits) and ground_truth.json.

Usage:
    python backend/generator.py --output data/evaluation/ --count 60
"""

import argparse
import csv
import json
import os
import random
from datetime import datetime, timedelta, date
from typing import Any


# ---------------------------------------------------------------------------
# Fee / tax formulas (integer paise, matching engine.py exactly)
# ---------------------------------------------------------------------------

def compute_fee(method: str, amount: int) -> int:
    """Expected fee: floor(amount * rate) + fixed. Pure integer arithmetic."""
    if method == "upi":
        return 0
    elif method == "card":
        return (amount * 2) // 100 + 100
    elif method == "netbanking":
        return (amount * 15) // 1000 + 100
    raise ValueError(f"Unknown method: {method}")


def compute_tax(fee: int) -> int:
    """Expected tax: floor(fee * 18 / 100). Pure integer arithmetic."""
    return (fee * 18) // 100


# ---------------------------------------------------------------------------
# Deterministic RNG
# ---------------------------------------------------------------------------

_rng: random.Random | None = None


def _get_rng(seed: int | None = None) -> random.Random:
    global _rng
    if seed is not None or _rng is None:
        _rng = random.Random(seed if seed is not None else 42)
    return _rng


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_METHODS = ["upi", "card", "netbanking"]


def _random_method(rng: random.Random) -> str:
    return rng.choice(_METHODS)


def _random_amount(rng: random.Random, lo: int = 50000, hi: int = 500000) -> int:
    return rng.randint(lo, hi)


def _iso(dt: datetime) -> str:
    """Output date-only format (YYYY-MM-DD) for compatibility with _parse_date."""
    return dt.strftime("%Y-%m-%d")


def _iso_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _ts(base: datetime, rng: random.Random, spread_hours: int = 48) -> datetime:
    return base + timedelta(hours=rng.randint(0, spread_hours))


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------

def _make_transaction(
    pid: str,
    amount: int,
    method: str,
    created_at: datetime,
    settlement_id: str | None = None,
    fee_override: int | None = None,
    tax_override: int | None = None,
) -> dict[str, Any]:
    fee = fee_override if fee_override is not None else compute_fee(method, amount)
    tax = tax_override if tax_override is not None else compute_tax(fee)
    return {
        "payment_id": pid,
        "order_id": f"ORD_{pid}",
        "amount": amount,
        "status": "captured",
        "method": method,
        "fee": fee,
        "tax": tax,
        "customer_email": f"user_{pid.lower()}@example.com",
        "created_at": _iso(created_at),
        "settlement_id": settlement_id,
    }


def _make_settlement(
    sid: str,
    amount: int,
    utr: str,
    created_at: datetime,
    settled_at: datetime,
    linked_payment_ids: list[str],
    linked_refund_ids: list[str],
) -> dict[str, Any]:
    return {
        "settlement_id": sid,
        "amount": amount,
        "status": "settled",
        "utr": utr,
        "created_at": _iso(created_at),
        "settled_at": _iso(settled_at),
        "linked_payment_ids": linked_payment_ids,
        "linked_refund_ids": linked_refund_ids,
    }


def _make_refund(
    rid: str,
    pid: str,
    amount: int,
    created_at: datetime,
) -> dict[str, Any]:
    return {
        "refund_id": rid,
        "payment_id": pid,
        "amount": amount,
        "status": "processed",
        "created_at": _iso(created_at),
    }


def _make_bank_credit(
    utr: str | None,
    amount: int,
    bc_date: date,
) -> dict[str, Any]:
    return {
        "utr": utr,
        "amount": amount,
        "date": _iso_date(bc_date),
        "description": f"Credit {utr or 'NO_UTR'}",
        "bank_account": "ACC001",
    }


def _settlement_amount(linked_payments: list[dict], linked_refunds: list[dict]) -> int:
    """Compute correct settlement amount = payments - refunds - fees - tax."""
    total_payments = sum(p["amount"] for p in linked_payments)
    total_refunds = sum(r["amount"] for r in linked_refunds)
    total_fees = sum(p["fee"] for p in linked_payments)
    total_tax = sum(p["tax"] for p in linked_payments)
    return total_payments - total_refunds - total_fees - total_tax


# ---------------------------------------------------------------------------
# Edge case generators
# ---------------------------------------------------------------------------

def _gen_clean_match(
    idx: int,
    rng: random.Random,
    base_date: datetime,
) -> tuple[dict, list[dict], list[dict], list[dict], dict]:
    """Generate a clean_match settlement. difference == 0, all checks pass."""
    sid = f"SETL_{idx:04d}"
    n_payments = rng.randint(1, 4)
    linked_pids = []
    transactions = []
    refunds = []
    total_payment = 0

    for j in range(n_payments):
        pid = f"PAY_{idx:04d}_{j:02d}"
        amount = _random_amount(rng)
        method = _random_method(rng)
        created_at = _ts(base_date, rng, 24)
        t = _make_transaction(pid, amount, method, created_at, settlement_id=sid)
        linked_pids.append(pid)
        transactions.append(t)
        total_payment += amount

    # Optionally add refunds (fees are never refunded, keep refund <= 40% of payment)
    linked_rids = []
    if rng.random() < 0.3:
        n_refunds = rng.randint(1, min(2, n_payments))
        for k in range(n_refunds):
            pid = rng.choice(linked_pids)
            pay_t = next(t for t in transactions if t["payment_id"] == pid)
            max_refund = max(100, pay_t["amount"] * 2 // 5)  # 40% cap
            refund_amount = rng.randint(100, max_refund)
            rid = f"REF_{idx:04d}_{k:02d}"
            refund_created = _ts(base_date, rng, 24)
            r = _make_refund(rid, pid, refund_amount, refund_created)
            linked_rids.append(rid)
            refunds.append(r)

    # Compute correct settlement amount
    linked_pay_dicts = [
        {"amount": t["amount"], "fee": t["fee"], "tax": t["tax"]}
        for t in transactions
    ]
    linked_ref_dicts = [
        {"amount": r["amount"]} for r in refunds
    ]
    settlement_amt = _settlement_amount(linked_pay_dicts, linked_ref_dicts)

    utr = f"UTR_{idx:04d}"
    created_at = _ts(base_date, rng, 12)
    settled_at = created_at + timedelta(hours=rng.randint(6, 24))
    settlement = _make_settlement(
        sid, settlement_amt, utr, created_at, settled_at, linked_pids, linked_rids,
    )

    bank_credit = _make_bank_credit(utr, settlement_amt, settled_at.date())

    gt = {
        "settlement_id": sid,
        "label": "clean_match",
        "expected_amount_paise": settlement_amt,
        "actual_amount_paise": settlement_amt,
        "difference_paise": 0,
    }
    return settlement, transactions, refunds, [bank_credit], gt


def _gen_missing_reference(
    idx: int,
    rng: random.Random,
    base_date: datetime,
) -> tuple[dict, list[dict], list[dict], list[dict], dict]:
    """Settlement references payment IDs that don't exist in transactions."""
    sid = f"SETL_{idx:04d}"
    n_ghosts = rng.randint(2, 4)
    ghost_pids = [f"PAY_GHOST_{idx:04d}_{j:02d}" for j in range(n_ghosts)]

    utr = f"UTR_{idx:04d}"
    amount = _random_amount(rng, 10000, 200000)
    created_at = _ts(base_date, rng, 12)
    settled_at = created_at + timedelta(hours=rng.randint(6, 24))

    settlement = _make_settlement(
        sid, amount, utr, created_at, settled_at, ghost_pids, [],
    )
    bank_credit = _make_bank_credit(utr, amount, settled_at.date())

    gt = {
        "settlement_id": sid,
        "label": "missing_reference",
        "expected_amount_paise": 0,
        "actual_amount_paise": amount,
        "difference_paise": amount,
    }
    return settlement, [], [], [bank_credit], gt


def _gen_duplicate_settlement(
    idx: int,
    rng: random.Random,
    base_date: datetime,
    existing_settlements: list[dict],
) -> tuple[dict, list[dict], list[dict], list[dict], dict]:
    """Generate a settlement that shares its ID with an earlier settlement."""
    # Pick a random earlier settlement to duplicate
    donor = rng.choice(existing_settlements)
    sid = donor["settlement_id"]

    # Create new payments for this duplicate
    n_payments = rng.randint(1, 3)
    linked_pids = []
    transactions = []
    for j in range(n_payments):
        pid = f"PAY_DUP_{idx:04d}_{j:02d}"
        amount = _random_amount(rng)
        method = _random_method(rng)
        created_at = _ts(base_date, rng, 24)
        t = _make_transaction(pid, amount, method, created_at, settlement_id=sid)
        linked_pids.append(pid)
        transactions.append(t)

    linked_pay_dicts = [
        {"amount": t["amount"], "fee": t["fee"], "tax": t["tax"]} for t in transactions
    ]
    settlement_amt = _settlement_amount(linked_pay_dicts, [])

    utr = f"UTR_DUP_{idx:04d}"
    created_at = _ts(base_date, rng, 12)
    settled_at = created_at + timedelta(hours=rng.randint(6, 24))
    settlement = _make_settlement(
        sid, settlement_amt, utr, created_at, settled_at, linked_pids, [],
    )
    bank_credit = _make_bank_credit(utr, settlement_amt, settled_at.date())

    gt = {
        "settlement_id": sid,
        "label": "duplicate_settlement",
        "expected_amount_paise": settlement_amt,
        "actual_amount_paise": settlement_amt,
        "difference_paise": 0,
    }
    return settlement, transactions, [], [bank_credit], gt


def _gen_bank_mismatch(
    idx: int,
    rng: random.Random,
    base_date: datetime,
) -> tuple[dict, list[dict], list[dict], list[dict], dict]:
    """Settlement exists but no matching bank credit found."""
    sid = f"SETL_{idx:04d}"
    n_payments = rng.randint(1, 3)
    linked_pids = []
    transactions = []
    for j in range(n_payments):
        pid = f"PAY_{idx:04d}_{j:02d}"
        amount = _random_amount(rng)
        method = _random_method(rng)
        created_at = _ts(base_date, rng, 24)
        t = _make_transaction(pid, amount, method, created_at, settlement_id=sid)
        linked_pids.append(pid)
        transactions.append(t)

    linked_pay_dicts = [
        {"amount": t["amount"], "fee": t["fee"], "tax": t["tax"]} for t in transactions
    ]
    settlement_amt = _settlement_amount(linked_pay_dicts, [])

    utr = f"UTR_{idx:04d}"
    created_at = _ts(base_date, rng, 12)
    settled_at = created_at + timedelta(hours=rng.randint(6, 24))
    settlement = _make_settlement(
        sid, settlement_amt, utr, created_at, settled_at, linked_pids, [],
    )

    # Bank credit with different UTR and different amount — won't match
    wrong_utr = f"UTR_WRONG_{idx:04d}"
    wrong_amount = settlement_amt + rng.randint(1000, 50000)
    bc = _make_bank_credit(wrong_utr, wrong_amount, settled_at.date() + timedelta(days=5))

    gt = {
        "settlement_id": sid,
        "label": "bank_mismatch",
        "expected_amount_paise": settlement_amt,
        "actual_amount_paise": settlement_amt,
        "difference_paise": 0,
    }
    return settlement, transactions, [], [bc], gt


def _gen_fee_mismatch(
    idx: int,
    rng: random.Random,
    base_date: datetime,
) -> tuple[dict, list[dict], list[dict], list[dict], dict]:
    """Transaction fee doesn't match the deterministic formula."""
    sid = f"SETL_{idx:04d}"
    n_payments = rng.randint(2, 4)
    linked_pids = []
    transactions = []
    mismatch_pid = None

    for j in range(n_payments):
        pid = f"PAY_{idx:04d}_{j:02d}"
        amount = _random_amount(rng)
        method = _random_method(rng)
        created_at = _ts(base_date, rng, 24)
        fee_correct = compute_fee(method, amount)

        if j == 0 and n_payments > 1:
            # Inject fee mismatch on first payment
            wrong_fee = fee_correct + rng.choice([-1, 1]) * max(1, rng.randint(1, 50))
            t = _make_transaction(pid, amount, method, created_at,
                                  settlement_id=sid, fee_override=wrong_fee)
            mismatch_pid = pid
        else:
            t = _make_transaction(pid, amount, method, created_at, settlement_id=sid)
        linked_pids.append(pid)
        transactions.append(t)

    # Compute expected amount using the (possibly wrong) fees from transactions
    linked_pay_dicts = [
        {"amount": t["amount"], "fee": t["fee"], "tax": t["tax"]} for t in transactions
    ]
    settlement_amt = _settlement_amount(linked_pay_dicts, [])

    utr = f"UTR_{idx:04d}"
    created_at = _ts(base_date, rng, 12)
    settled_at = created_at + timedelta(hours=rng.randint(6, 24))
    settlement = _make_settlement(
        sid, settlement_amt, utr, created_at, settled_at, linked_pids, [],
    )
    bank_credit = _make_bank_credit(utr, settlement_amt, settled_at.date())

    # Ground truth: expected uses CORRECT fees, actual = settlement amount
    correct_linked = []
    for t in transactions:
        method = t["method"]
        correct_fee = compute_fee(method, t["amount"])
        correct_tax = compute_tax(correct_fee)
        correct_linked.append({"amount": t["amount"], "fee": correct_fee, "tax": correct_tax})
    gt_expected = _settlement_amount(correct_linked, [])

    gt = {
        "settlement_id": sid,
        "label": "fee_mismatch",
        "expected_amount_paise": gt_expected,
        "actual_amount_paise": settlement_amt,
        "difference_paise": settlement_amt - gt_expected,
        "mismatched_payment_id": mismatch_pid,
    }
    return settlement, transactions, [], [bank_credit], gt


def _gen_tax_inconsistency(
    idx: int,
    rng: random.Random,
    base_date: datetime,
) -> tuple[dict, list[dict], list[dict], list[dict], dict]:
    """Transaction tax doesn't match floor(fee * 0.18)."""
    sid = f"SETL_{idx:04d}"
    n_payments = rng.randint(2, 4)
    linked_pids = []
    transactions = []
    mismatch_pid = None

    for j in range(n_payments):
        pid = f"PAY_{idx:04d}_{j:02d}"
        amount = _random_amount(rng)
        method = _random_method(rng)
        created_at = _ts(base_date, rng, 24)
        fee = compute_fee(method, amount)
        correct_tax = compute_tax(fee)

        if j == 0 and n_payments > 1:
            # Inject tax inconsistency
            wrong_tax = correct_tax + rng.choice([-1, 1]) * max(1, rng.randint(1, 20))
            if wrong_tax < 0:
                wrong_tax = correct_tax + rng.randint(1, 20)
            t = _make_transaction(pid, amount, method, created_at,
                                  settlement_id=sid, tax_override=wrong_tax)
            mismatch_pid = pid
        else:
            t = _make_transaction(pid, amount, method, created_at, settlement_id=sid)
        linked_pids.append(pid)
        transactions.append(t)

    linked_pay_dicts = [
        {"amount": t["amount"], "fee": t["fee"], "tax": t["tax"]} for t in transactions
    ]
    settlement_amt = _settlement_amount(linked_pay_dicts, [])

    utr = f"UTR_{idx:04d}"
    created_at = _ts(base_date, rng, 12)
    settled_at = created_at + timedelta(hours=rng.randint(6, 24))
    settlement = _make_settlement(
        sid, settlement_amt, utr, created_at, settled_at, linked_pids, [],
    )
    bank_credit = _make_bank_credit(utr, settlement_amt, settled_at.date())

    # Ground truth: expected uses CORRECT tax
    correct_linked = []
    for t in transactions:
        fee = compute_fee(t["method"], t["amount"])
        correct_linked.append({"amount": t["amount"], "fee": fee, "tax": compute_tax(fee)})
    gt_expected = _settlement_amount(correct_linked, [])

    gt = {
        "settlement_id": sid,
        "label": "tax_inconsistency",
        "expected_amount_paise": gt_expected,
        "actual_amount_paise": settlement_amt,
        "difference_paise": settlement_amt - gt_expected,
        "mismatched_payment_id": mismatch_pid,
    }
    return settlement, transactions, [], [bank_credit], gt


def _gen_refund_timing(
    idx: int,
    rng: random.Random,
    base_date: datetime,
) -> tuple[dict, list[dict], list[dict], list[dict], dict]:
    """Refund created very close to settlement boundary. Produces MATH_DISCREPANCY."""
    sid = f"SETL_{idx:04d}"
    n_payments = rng.randint(2, 3)
    linked_pids = []
    transactions = []

    for j in range(n_payments):
        pid = f"PAY_{idx:04d}_{j:02d}"
        amount = _random_amount(rng, 20000, 300000)
        method = _random_method(rng)
        created_at = _ts(base_date, rng, 24)
        t = _make_transaction(pid, amount, method, created_at, settlement_id=sid)
        linked_pids.append(pid)
        transactions.append(t)

    # Refund created very close to settlement (within 24h boundary)
    linked_rids = []
    refund_list = []
    n_refunds = rng.randint(1, min(2, n_payments))
    for k in range(n_refunds):
        pid = rng.choice(linked_pids)
        pay_t = next(t for t in transactions if t["payment_id"] == pid)
        refund_amount = rng.randint(500, min(pay_t["amount"], 10000))
        rid = f"REF_{idx:04d}_{k:02d}"
        # Refund created within 24h of settlement boundary
        refund_created = base_date + timedelta(hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
        r = _make_refund(rid, pid, refund_amount, refund_created)
        linked_rids.append(rid)
        refund_list.append(r)

    # Compute expected amount
    linked_pay_dicts = [
        {"amount": t["amount"], "fee": t["fee"], "tax": t["tax"]} for t in transactions
    ]
    linked_ref_dicts = [{"amount": r["amount"]} for r in refund_list]
    expected = _settlement_amount(linked_pay_dicts, linked_ref_dicts)

    # Actual settlement differs by a small random amount (triggers MATH_DISCREPANCY)
    discrepancy = rng.randint(100, 5000) * rng.choice([-1, 1])
    actual = expected + discrepancy
    if actual <= 0:
        actual = expected + abs(discrepancy) + 1000

    utr = f"UTR_{idx:04d}"
    created_at = _ts(base_date, rng, 12)
    settled_at = created_at + timedelta(hours=rng.randint(6, 24))
    settlement = _make_settlement(
        sid, actual, utr, created_at, settled_at, linked_pids, linked_rids,
    )
    bank_credit = _make_bank_credit(utr, actual, settled_at.date())

    gt = {
        "settlement_id": sid,
        "label": "refund_timing",
        "expected_amount_paise": expected,
        "actual_amount_paise": actual,
        "difference_paise": actual - expected,
    }
    return settlement, transactions, refund_list, [bank_credit], gt


def _gen_unexplained(
    idx: int,
    rng: random.Random,
    base_date: datetime,
) -> tuple[dict, list[dict], list[dict], list[dict], dict]:
    """All checks pass but difference != 0. No obvious cause."""
    sid = f"SETL_{idx:04d}"
    n_payments = rng.randint(2, 4)
    linked_pids = []
    transactions = []

    for j in range(n_payments):
        pid = f"PAY_{idx:04d}_{j:02d}"
        amount = _random_amount(rng)
        method = _random_method(rng)
        created_at = _ts(base_date, rng, 24)
        t = _make_transaction(pid, amount, method, created_at, settlement_id=sid)
        linked_pids.append(pid)
        transactions.append(t)

    linked_pay_dicts = [
        {"amount": t["amount"], "fee": t["fee"], "tax": t["tax"]} for t in transactions
    ]
    expected = _settlement_amount(linked_pay_dicts, [])

    # Unexplained discrepancy
    discrepancy = rng.randint(500, 10000) * rng.choice([-1, 1])
    actual = expected + discrepancy
    if actual <= 0:
        actual = expected + abs(discrepancy) + 1000

    utr = f"UTR_{idx:04d}"
    created_at = _ts(base_date, rng, 12)
    settled_at = created_at + timedelta(hours=rng.randint(6, 24))
    settlement = _make_settlement(
        sid, actual, utr, created_at, settled_at, linked_pids, [],
    )
    bank_credit = _make_bank_credit(utr, actual, settled_at.date())

    gt = {
        "settlement_id": sid,
        "label": "unexplained",
        "expected_amount_paise": expected,
        "actual_amount_paise": actual,
        "difference_paise": actual - expected,
    }
    return settlement, transactions, [], [bank_credit], gt


# ---------------------------------------------------------------------------
# Batch generator
# ---------------------------------------------------------------------------

def generate_batch(
    n_settlements: int = 60,
    edge_cases: dict[str, int] | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Generate a complete evaluation dataset with ground-truth labels.

    Returns dict with keys: settlements, transactions, refunds, bank_credits, ground_truth.
    """
    if edge_cases is None:
        edge_cases = {
            "clean_match": 30,
            "missing_reference": 5,
            "duplicate_settlement": 2,
            "bank_mismatch": 5,
            "fee_mismatch": 5,
            "tax_inconsistency": 3,
            "refund_timing": 5,
            "unexplained": 5,
        }

    total_edge = sum(edge_cases.values())
    if total_edge != n_settlements:
        raise ValueError(
            f"Edge case total ({total_edge}) != n_settlements ({n_settlements})"
        )

    rng = _get_rng(seed)
    base_date = datetime(2026, 8, 15, 8, 0, 0)

    all_settlements: list[dict] = []
    all_transactions: list[dict] = []
    all_refunds: list[dict] = []
    all_bank_credits: list[dict] = []
    all_ground_truth: list[dict] = []

    idx = 1

    # 1. Clean matches
    for _ in range(edge_cases["clean_match"]):
        s, txns, refs, bcs, gt = _gen_clean_match(idx, rng, base_date)
        all_settlements.append(s)
        all_transactions.extend(txns)
        all_refunds.extend(refs)
        all_bank_credits.extend(bcs)
        all_ground_truth.append(gt)
        idx += 1

    # 2. Missing references
    for _ in range(edge_cases["missing_reference"]):
        s, txns, refs, bcs, gt = _gen_missing_reference(idx, rng, base_date)
        all_settlements.append(s)
        all_transactions.extend(txns)
        all_refunds.extend(refs)
        all_bank_credits.extend(bcs)
        all_ground_truth.append(gt)
        idx += 1

    # 3. Duplicate settlements (each shares ID with an earlier settlement)
    for _ in range(edge_cases["duplicate_settlement"]):
        dup_settlement, dup_txns, dup_refs, dup_bcs, dup_gt = _gen_duplicate_settlement(
            idx, rng, base_date, all_settlements,
        )
        all_settlements.append(dup_settlement)
        all_transactions.extend(dup_txns)
        all_refunds.extend(dup_refs)
        all_bank_credits.extend(dup_bcs)
        all_ground_truth.append(dup_gt)
        idx += 1

    # 4. Bank mismatches
    for _ in range(edge_cases["bank_mismatch"]):
        s, txns, refs, bcs, gt = _gen_bank_mismatch(idx, rng, base_date)
        all_settlements.append(s)
        all_transactions.extend(txns)
        all_refunds.extend(refs)
        all_bank_credits.extend(bcs)
        all_ground_truth.append(gt)
        idx += 1

    # 5. Fee mismatches
    for _ in range(edge_cases["fee_mismatch"]):
        s, txns, refs, bcs, gt = _gen_fee_mismatch(idx, rng, base_date)
        all_settlements.append(s)
        all_transactions.extend(txns)
        all_refunds.extend(refs)
        all_bank_credits.extend(bcs)
        all_ground_truth.append(gt)
        idx += 1

    # 6. Tax inconsistencies
    for _ in range(edge_cases["tax_inconsistency"]):
        s, txns, refs, bcs, gt = _gen_tax_inconsistency(idx, rng, base_date)
        all_settlements.append(s)
        all_transactions.extend(txns)
        all_refunds.extend(refs)
        all_bank_credits.extend(bcs)
        all_ground_truth.append(gt)
        idx += 1

    # 7. Refund timing
    for _ in range(edge_cases["refund_timing"]):
        s, txns, refs, bcs, gt = _gen_refund_timing(idx, rng, base_date)
        all_settlements.append(s)
        all_transactions.extend(txns)
        all_refunds.extend(refs)
        all_bank_credits.extend(bcs)
        all_ground_truth.append(gt)
        idx += 1

    # 8. Unexplained
    for _ in range(edge_cases["unexplained"]):
        s, txns, refs, bcs, gt = _gen_unexplained(idx, rng, base_date)
        all_settlements.append(s)
        all_transactions.extend(txns)
        all_refunds.extend(refs)
        all_bank_credits.extend(bcs)
        all_ground_truth.append(gt)
        idx += 1

    return {
        "settlements": all_settlements,
        "transactions": all_transactions,
        "refunds": all_refunds,
        "bank_credits": all_bank_credits,
        "ground_truth": all_ground_truth,
    }


# ---------------------------------------------------------------------------
# CSV / JSON writers
# ---------------------------------------------------------------------------

TRANSACTION_FIELDS = [
    "payment_id", "order_id", "amount", "status", "method",
    "fee", "tax", "customer_email", "created_at", "settlement_id",
]
SETTLEMENT_FIELDS = [
    "settlement_id", "amount", "status", "utr", "created_at",
    "settled_at", "linked_payment_ids", "linked_refund_ids",
]
REFUND_FIELDS = [
    "refund_id", "payment_id", "amount", "status", "created_at",
]
BANK_CREDIT_FIELDS = [
    "utr", "amount", "date", "description", "bank_account",
]


def _write_csv(path: str, fieldnames: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            serialized = {}
            for k, v in row.items():
                if isinstance(v, list):
                    serialized[k] = json.dumps(v)
                else:
                    serialized[k] = v
            writer.writerow(serialized)


def write_dataset(data: dict[str, Any], output_dir: str) -> None:
    """Write generated data to CSV files and ground_truth.json."""
    os.makedirs(output_dir, exist_ok=True)

    _write_csv(
        os.path.join(output_dir, "transactions.csv"),
        TRANSACTION_FIELDS,
        data["transactions"],
    )
    _write_csv(
        os.path.join(output_dir, "settlements.csv"),
        SETTLEMENT_FIELDS,
        data["settlements"],
    )
    _write_csv(
        os.path.join(output_dir, "refunds.csv"),
        REFUND_FIELDS,
        data["refunds"],
    )
    _write_csv(
        os.path.join(output_dir, "bank_credits.csv"),
        BANK_CREDIT_FIELDS,
        data["bank_credits"],
    )

    gt_path = os.path.join(output_dir, "ground_truth.json")
    with open(gt_path, "w", encoding="utf-8") as f:
        json.dump(data["ground_truth"], f, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic settlement data with ground-truth labels.",
    )
    parser.add_argument(
        "--output", "-o",
        default="data/evaluation/",
        help="Output directory (default: data/evaluation/)",
    )
    parser.add_argument(
        "--count", "-n",
        type=int,
        default=60,
        help="Total number of settlements to generate (default: 60)",
    )
    parser.add_argument(
        "--seed", "-s",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    args = parser.parse_args()

    # Default distribution
    edge_cases = {
        "clean_match": 30,
        "missing_reference": 5,
        "duplicate_settlement": 2,
        "bank_mismatch": 5,
        "fee_mismatch": 5,
        "tax_inconsistency": 3,
        "refund_timing": 5,
        "unexplained": 5,
    }

    data = generate_batch(n_settlements=args.count, edge_cases=edge_cases, seed=args.seed)
    write_dataset(data, args.output)

    # Print summary
    labels = [gt["label"] for gt in data["ground_truth"]]
    from collections import Counter
    dist = Counter(labels)

    print(f"Generated {len(data['settlements'])} settlements → {args.output}")
    print(f"  transactions.csv:  {len(data['transactions'])} rows")
    print(f"  settlements.csv:   {len(data['settlements'])} rows")
    print(f"  refunds.csv:       {len(data['refunds'])} rows")
    print(f"  bank_credits.csv:  {len(data['bank_credits'])} rows")
    print(f"  ground_truth.json: {len(data['ground_truth'])} entries")
    print(f"\nLabel distribution:")
    for label, count in sorted(dist.items()):
        print(f"  {label}: {count}")


if __name__ == "__main__":
    main()
