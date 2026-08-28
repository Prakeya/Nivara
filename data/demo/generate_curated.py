#!/usr/bin/env python3
"""Generate a curated 10-settlement demo dataset that hits every decision path.

Engine check order:
  1. schema_validation  2. duplicate_detection  3. reference_existence
  4. linkage_consistency  5. fee_validation  6. tax_validation
  7. bank_credit_existence  8. utr_cross_check  9. amount_cross_check
  10-11. expected_amount_calculation, difference_calculation

Bank credit linking: UTR match + amount match + date within 2 days.

Paths covered:
  SETL_001: CLEAN_MATCH (UPI, no refunds)
  SETL_002: CLEAN_MATCH (CARD, fees correct)
  SETL_003: DETERMINISTIC_EXCEPTION: bank_credit_existence (no bank credit)
  SETL_004: DETERMINISTIC_EXCEPTION: utr_cross_check (bank credit has wrong UTR)
  SETL_005: DETERMINISTIC_EXCEPTION: fee_validation (payment has wrong fee)
  SETL_006: DETERMINISTIC_EXCEPTION: tax_inconsistency (payment has wrong tax)
  SETL_007: MATH_DISCREPANCY → AI: REFUND_TIMING (has linked refunds, difference)
  SETL_008: MATH_DISCREPANCY → AI: TIMING_MISMATCH (all checks pass, delay in bank credit within 2-day window)
  SETL_009: MATH_DISCREPANCY → AI: UNEXPLAINED (all checks pass, no clear cause)
  SETL_010: DETERMINISTIC_EXCEPTION: duplicate_detection (same UTR as SETL_003 in settlements)
"""

import csv
import json
import os
from datetime import datetime, timedelta

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def compute_fee(method: str, amount: int) -> int:
    if method == "upi":
        return 0
    elif method == "card":
        return (amount * 2) // 100 + 100
    elif method == "netbanking":
        return (amount * 15) // 1000 + 100
    return 0


def compute_tax(fee: int) -> int:
    return (fee * 18) // 100


BASE = datetime(2026, 8, 20, 10, 0, 0)

# ── Payments (with intentional errors for fee/tax checks) ──
payments = [
    {"payment_id": "PAY_001", "order_id": "ORD_001", "amount": 50000, "method": "upi"},
    {"payment_id": "PAY_002", "order_id": "ORD_002", "amount": 80000, "method": "card"},
    {"payment_id": "PAY_003", "order_id": "ORD_003", "amount": 120000, "method": "upi"},
    {"payment_id": "PAY_004", "order_id": "ORD_004", "amount": 45000, "method": "card"},
    {"payment_id": "PAY_005", "order_id": "ORD_005", "amount": 95000, "method": "upi"},
    {"payment_id": "PAY_006", "order_id": "ORD_006", "amount": 60000, "method": "card"},
    {"payment_id": "PAY_007", "order_id": "ORD_007", "amount": 75000, "method": "upi"},
    {"payment_id": "PAY_008", "order_id": "ORD_008", "amount": 55000, "method": "card"},
    {"payment_id": "PAY_009", "order_id": "ORD_009", "amount": 40000, "method": "upi"},
    {"payment_id": "PAY_010", "order_id": "ORD_010", "amount": 30000, "method": "card"},
]

# Compute correct fees/taxes
for p in payments:
    p["fee"] = compute_fee(p["method"], p["amount"])
    p["tax"] = compute_tax(p["fee"])
    p["status"] = "captured"
    p["created_at"] = _iso(BASE)

# Intentionally WRONG fee for SETL_005 (PAY_005 is UPI, correct fee=0)
payments[4]["fee"] = 100
payments[4]["tax"] = compute_tax(100)

# Intentionally WRONG tax for SETL_006 (PAY_006 is CARD, correct tax=234)
payments[5]["tax"] = 200

# Fix PAY_010 to share payment_id with PAY_003 (triggers duplicate detection)
payments[9]["payment_id"] = "PAY_003"
payments[9]["order_id"] = "ORD_003"

# ── Refunds ──
refunds = [
    {"refund_id": "REF_007", "payment_id": "PAY_007", "amount": 5000,
     "status": "processed", "created_at": _iso(BASE + timedelta(hours=6))},
    {"refund_id": "REF_009", "payment_id": "PAY_009", "amount": 3000,
     "status": "processed", "created_at": _iso(BASE + timedelta(hours=4))},
]

# ── Compute expected amounts (engine uses CORRECT fees) ──
def expected_amount(payment_ids, refund_ids):
    pays = [p for p in payments if p["payment_id"] in payment_ids]
    refs = [r for r in refunds if r["refund_id"] in refund_ids]
    total_pay = sum(p["amount"] for p in pays)
    total_ref = sum(r["amount"] for r in refs)
    total_fee = sum(compute_fee(p["method"], p["amount"]) for p in pays)
    total_tax = sum(compute_tax(compute_fee(p["method"], p["amount"])) for p in pays)
    return total_pay - total_ref - total_fee - total_tax

s001 = expected_amount(["PAY_001"], [])
s002 = expected_amount(["PAY_002"], [])
s003 = expected_amount(["PAY_003"], [])
s004 = expected_amount(["PAY_004"], [])
s005 = expected_amount(["PAY_005"], [])
s006 = expected_amount(["PAY_006"], [])
s007 = expected_amount(["PAY_007"], ["REF_007"])
s008 = expected_amount(["PAY_008"], [])
s009 = expected_amount(["PAY_009"], ["REF_009"])
s010 = expected_amount(["PAY_010"], [])

# ── Settlements ──
settlements = [
    # CLEAN_MATCH
    {"settlement_id": "SETL_001", "amount": s001, "status": "settled", "utr": "UTR_DEMO_001",
     "created_at": _iso(BASE), "settled_at": _iso(BASE + timedelta(days=1)),
     "linked_payment_ids": '["PAY_001"]', "linked_refund_ids": '[]'},
    # CLEAN_MATCH (CARD)
    {"settlement_id": "SETL_002", "amount": s002, "status": "settled", "utr": "UTR_DEMO_002",
     "created_at": _iso(BASE), "settled_at": _iso(BASE + timedelta(days=1)),
     "linked_payment_ids": '["PAY_002"]', "linked_refund_ids": '[]'},
    # bank_credit_existence (no bank credit)
    {"settlement_id": "SETL_003", "amount": s003, "status": "settled", "utr": "UTR_DEMO_003",
     "created_at": _iso(BASE), "settled_at": _iso(BASE + timedelta(days=1)),
     "linked_payment_ids": '["PAY_003"]', "linked_refund_ids": '[]'},
    # utr_cross_check (bank credit has wrong UTR)
    {"settlement_id": "SETL_004", "amount": s004, "status": "settled", "utr": "UTR_DEMO_004",
     "created_at": _iso(BASE), "settled_at": _iso(BASE + timedelta(days=1)),
     "linked_payment_ids": '["PAY_004"]', "linked_refund_ids": '[]'},
    # fee_validation (payment fee is wrong)
    {"settlement_id": "SETL_005", "amount": s005, "status": "settled", "utr": "UTR_DEMO_005",
     "created_at": _iso(BASE), "settled_at": _iso(BASE + timedelta(days=1)),
     "linked_payment_ids": '["PAY_005"]', "linked_refund_ids": '[]'},
    # tax_inconsistency (payment tax is wrong)
    {"settlement_id": "SETL_006", "amount": s006, "status": "settled", "utr": "UTR_DEMO_006",
     "created_at": _iso(BASE), "settled_at": _iso(BASE + timedelta(days=1)),
     "linked_payment_ids": '["PAY_006"]', "linked_refund_ids": '[]'},
    # MATH_DISCREPANCY → REFUND_TIMING
    {"settlement_id": "SETL_007", "amount": s007 - 2000, "status": "settled", "utr": "UTR_DEMO_007",
     "created_at": _iso(BASE), "settled_at": _iso(BASE + timedelta(days=1)),
     "linked_payment_ids": '["PAY_007"]', "linked_refund_ids": '["REF_007"]'},
    # MATH_DISCREPANCY → TIMING_MISMATCH (bank credit delayed, actual differs from expected)
    {"settlement_id": "SETL_008", "amount": s008 - 584, "status": "settled", "utr": "UTR_DEMO_008",
     "created_at": _iso(BASE), "settled_at": _iso(BASE + timedelta(days=1)),
     "linked_payment_ids": '["PAY_008"]', "linked_refund_ids": '[]'},
    # MATH_DISCREPANCY → UNEXPLAINED
    {"settlement_id": "SETL_009", "amount": s009 - 500, "status": "settled", "utr": "UTR_DEMO_009",
     "created_at": _iso(BASE), "settled_at": _iso(BASE + timedelta(days=1)),
     "linked_payment_ids": '["PAY_009"]', "linked_refund_ids": '["REF_009"]'},
    # duplicate_detection (PAY_010 shares payment_id with PAY_003)
    {"settlement_id": "SETL_010", "amount": s010, "status": "settled", "utr": "UTR_DUP_010",
     "created_at": _iso(BASE), "settled_at": _iso(BASE + timedelta(days=1)),
     "linked_payment_ids": '["PAY_010"]', "linked_refund_ids": '[]'},
]

# ── Bank credits ──
bank_credits = [
    # SETL_001: clean match
    {"settlement_id": "SETL_001", "utr": "UTR_DEMO_001", "amount": s001,
     "date": _date(BASE + timedelta(days=2)), "description": "bank credit"},
    # SETL_002: clean match
    {"settlement_id": "SETL_002", "utr": "UTR_DEMO_002", "amount": s002,
     "date": _date(BASE + timedelta(days=2)), "description": "bank credit"},
    # SETL_003: NO bank credit (triggers bank_credit_existence)
    # SETL_004: wrong UTR (triggers utr_cross_check)
    {"settlement_id": "SETL_004", "utr": "UTR_WRONG", "amount": s004,
     "date": _date(BASE + timedelta(days=2)), "description": "bank credit"},
    # SETL_005: correct bank credit (fee error is in payment)
    {"settlement_id": "SETL_005", "utr": "UTR_DEMO_005", "amount": s005,
     "date": _date(BASE + timedelta(days=2)), "description": "bank credit"},
    # SETL_006: correct bank credit (tax error is in payment)
    {"settlement_id": "SETL_006", "utr": "UTR_DEMO_006", "amount": s006,
     "date": _date(BASE + timedelta(days=2)), "description": "bank credit"},
    # SETL_007: bank credit matches actual (s007 - 2000)
    {"settlement_id": "SETL_007", "utr": "UTR_DEMO_007", "amount": s007 - 2000,
     "date": _date(BASE + timedelta(days=2)), "description": "bank credit"},
    # SETL_008: bank credit delayed 2 days (within window, but bank_credited_at > settled_at + expected_cycle_days)
    {"settlement_id": "SETL_008", "utr": "UTR_DEMO_008", "amount": s008,
     "date": _date(BASE + timedelta(days=3)), "description": "bank credit"},
    # SETL_009: bank credit matches actual (s009 - 500)
    {"settlement_id": "SETL_009", "utr": "UTR_DEMO_009", "amount": s009 - 500,
     "date": _date(BASE + timedelta(days=2)), "description": "bank credit"},
    # SETL_010: bank credit exists (duplicate_detection happens first)
    {"settlement_id": "SETL_010", "utr": "UTR_DEMO_010", "amount": s010,
     "date": _date(BASE + timedelta(days=2)), "description": "bank credit"},
]

# ── Ground truth ──
ground_truth = [
    {"settlement_id": "SETL_001", "label": "clean_match",
     "expected_amount_paise": s001, "actual_amount_paise": s001, "difference_paise": 0},
    {"settlement_id": "SETL_002", "label": "clean_match",
     "expected_amount_paise": s002, "actual_amount_paise": s002, "difference_paise": 0},
    {"settlement_id": "SETL_003", "label": "missing_reference",
     "expected_amount_paise": s003, "actual_amount_paise": s003, "difference_paise": 0},
    {"settlement_id": "SETL_004", "label": "bank_mismatch",
     "expected_amount_paise": s004, "actual_amount_paise": s004, "difference_paise": 0},
    {"settlement_id": "SETL_005", "label": "fee_mismatch",
     "expected_amount_paise": s005, "actual_amount_paise": s005, "difference_paise": 0},
    {"settlement_id": "SETL_006", "label": "tax_inconsistency",
     "expected_amount_paise": s006, "actual_amount_paise": s006, "difference_paise": 0},
    {"settlement_id": "SETL_007", "label": "refund_timing",
     "expected_amount_paise": s007, "actual_amount_paise": s007 - 2000, "difference_paise": -2000},
    {"settlement_id": "SETL_008", "label": "timing_mismatch",
     "expected_amount_paise": s008, "actual_amount_paise": s008, "difference_paise": 0},
    {"settlement_id": "SETL_009", "label": "unexplained",
     "expected_amount_paise": s009, "actual_amount_paise": s009 - 500, "difference_paise": -500},
    {"settlement_id": "SETL_010", "label": "same_day_duplicates",
     "expected_amount_paise": s010, "actual_amount_paise": s010, "difference_paise": 0},
]


def write_csv(filename, rows):
    path = os.path.join(OUTPUT_DIR, filename)
    if not rows:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {filename}")


if __name__ == "__main__":
    write_csv("transactions.csv", payments)
    write_csv("settlements.csv", settlements)
    write_csv("refunds.csv", refunds)
    write_csv("bank_credits.csv", bank_credits)
    gt_path = os.path.join(OUTPUT_DIR, "ground_truth.json")
    with open(gt_path, "w") as f:
        json.dump(ground_truth, f, indent=2)
    print(f"Wrote {len(ground_truth)} entries to ground_truth.json")
    print("Demo dataset: 10 settlements covering every decision path")
