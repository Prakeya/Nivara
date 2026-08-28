"""
Phase 3: Entity Linking

Links entities across the 4 CSV sources:
- payment_id → transaction
- settlement → linked_payment_ids (authoritative)
- settlement → linked_refund_ids
- settlement → bank_credit (UTR primary, amount+date fallback)

Detects linkage errors:
- MISSING_REFERENCE: Linked ID not found in uploaded data
- LINKAGE_MISMATCH: transactions.settlement_id disagrees with settlements.linked_payment_ids
- ORPHAN_PAYMENT: Payment has settlement_id pointing to non-existent settlement
- PAYMENT_OVERCLAIM: Same payment_id in multiple settlements' linked_payment_ids
- REFUND_OVERAGE: Sum of refunds for a payment > payment amount
"""

from datetime import date, timedelta
from typing import Optional
from enum import Enum


class LinkageError(str, Enum):
    MISSING_REFERENCE = "MISSING_REFERENCE"
    LINKAGE_MISMATCH = "LINKAGE_MISMATCH"
    ORPHAN_PAYMENT = "ORPHAN_PAYMENT"
    PAYMENT_OVERCLAIM = "PAYMENT_OVERCLAIM"
    REFUND_OVERAGE = "REFUND_OVERAGE"
    BANK_MISMATCH = "BANK_MISMATCH"


class LinkageResult:
    """Result of entity linking for a single settlement."""

    def __init__(self, settlement_id: str):
        self.settlement_id = settlement_id
        self.linked_payments: list[dict] = []
        self.linked_refunds: list[dict] = []
        self.bank_credit: Optional[dict] = None
        self.errors: list[dict] = []

    def add_error(self, error_type: LinkageError, message: str, field: str = "", entity_id: str = ""):
        self.errors.append({
            "error_type": error_type.value,
            "message": message,
            "field": field,
            "entity_id": entity_id,
        })

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


def build_payment_index(transactions: list[dict]) -> dict[str, dict]:
    """Build payment_id → transaction index."""
    return {t["payment_id"]: t for t in transactions}


def build_refund_index(refunds: list[dict]) -> dict[str, list[dict]]:
    """Build refund_id → refund index."""
    return {r["refund_id"]: r for r in refunds}


def build_refunds_by_payment_index(refunds: list[dict]) -> dict[str, list[dict]]:
    """Build payment_id → list of refunds index."""
    index: dict[str, list[dict]] = {}
    for r in refunds:
        pid = r["payment_id"]
        if pid not in index:
            index[pid] = []
        index[pid].append(r)
    return index


def detect_payment_overclaims(settlements: list[dict]) -> list[dict]:
    """
    Detect PAYMENT_OVERCLAIM: Same payment_id in multiple settlements' linked_payment_ids.
    Returns list of error dicts.
    """
    payment_to_settlements: dict[str, list[str]] = {}
    for s in settlements:
        for pid in s["linked_payment_ids"]:
            if pid not in payment_to_settlements:
                payment_to_settlements[pid] = []
            payment_to_settlements[pid].append(s["settlement_id"])

    errors = []
    for pid, sids in payment_to_settlements.items():
        if len(sids) > 1:
            errors.append({
                "error_type": LinkageError.PAYMENT_OVERCLAIM.value,
                "message": f"Payment {pid} appears in multiple settlements: {', '.join(sids)}",
                "field": "linked_payment_ids",
                "entity_id": pid,
            })
    return errors


def detect_refund_overages(
    transactions: list[dict],
    refunds: list[dict],
) -> list[dict]:
    """
    Detect REFUND_OVERAGE: Sum of refunds for a payment > payment amount.
    Returns list of error dicts.
    """
    payment_amounts = {t["payment_id"]: t["amount"] for t in transactions}
    refund_totals: dict[str, int] = {}
    for r in refunds:
        pid = r["payment_id"]
        refund_totals[pid] = refund_totals.get(pid, 0) + r["amount"]

    errors = []
    for pid, total in refund_totals.items():
        if pid in payment_amounts and total > payment_amounts[pid]:
            errors.append({
                "error_type": LinkageError.REFUND_OVERAGE.value,
                "message": f"Sum of refunds ({total}) for payment {pid} exceeds payment amount ({payment_amounts[pid]})",
                "field": "amount",
                "entity_id": pid,
            })
    return errors


def detect_cross_check_mismatches(
    transactions: list[dict],
    settlements: list[dict],
) -> list[dict]:
    """
    Detect LINKAGE_MISMATCH: transactions.settlement_id disagrees with
    settlements.linked_payment_ids.

    For each transaction with a settlement_id set:
    - Verify that settlement exists
    - Verify that transaction's payment_id is in that settlement's linked_payment_ids
    """
    settlement_index = {s["settlement_id"]: s for s in settlements}
    errors = []

    for t in transactions:
        tsid = t.get("settlement_id")
        if not tsid:
            continue

        if tsid not in settlement_index:
            errors.append({
                "error_type": LinkageError.ORPHAN_PAYMENT.value,
                "message": f"Transaction {t['payment_id']} references non-existent settlement {tsid}",
                "field": "settlement_id",
                "entity_id": t["payment_id"],
                "settlement_id": tsid,
            })
            continue

        settlement = settlement_index[tsid]
        if t["payment_id"] not in settlement["linked_payment_ids"]:
            errors.append({
                "error_type": LinkageError.LINKAGE_MISMATCH.value,
                "message": (
                    f"Transaction {t['payment_id']} has settlement_id={tsid} "
                    f"but is NOT in settlement's linked_payment_ids"
                ),
                "field": "settlement_id",
                "entity_id": t["payment_id"],
                "settlement_id": tsid,
            })

    return errors


def _parse_date(d) -> date:
    """Parse a date from various formats into a date object."""
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(d, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Invalid date format: {d}")
    raise TypeError(f"Cannot parse date from type {type(d)}")


from datetime import datetime


def _dates_within_2_days(d1: date, d2: date) -> bool:
    """Check if two dates are within 2 days of each other."""
    return abs((d1 - d2).days) <= 2


def link_bank_credit(
    settlement: dict,
    bank_credits: list[dict],
) -> Optional[dict]:
    """
    Link a bank credit to a settlement.

    Primary: settlements.utr == bank_credits.utr + amount match + date within 2 days
    Fallback: amount match + date within 2 days (if UTR missing/none)
    Failure: None returned
    """
    settlement_utr = settlement.get("utr")
    settlement_amount = settlement["amount"]
    settled_at = _parse_date(settlement["settled_at"])

    # Primary: UTR match + amount match + date within 2 days
    if settlement_utr:
        for bc in bank_credits:
            bc_utr = bc.get("utr")
            if bc_utr and bc_utr == settlement_utr:
                if bc["amount"] == settlement_amount:
                    bc_date = _parse_date(bc["date"])
                    if _dates_within_2_days(settled_at, bc_date):
                        return bc

    # Fallback: amount match + date within 2 days (for bank credits with no UTR)
    for bc in bank_credits:
        bc_utr = bc.get("utr")
        if bc_utr is None or bc_utr == "":
            if bc["amount"] == settlement_amount:
                bc_date = _parse_date(bc["date"])
                if _dates_within_2_days(settled_at, bc_date):
                    return bc

    return None


def link_settlement(
    settlement: dict,
    payment_index: dict[str, dict],
    refund_index: dict[str, dict],
    refunds_by_payment: dict[str, list[dict]],
    bank_credits: list[dict],
) -> LinkageResult:
    """
    Link all entities for a single settlement.

    Returns a LinkageResult with linked payments, refunds, bank credit, and any errors.
    """
    result = LinkageResult(settlement["settlement_id"])

    # 1. Resolve linked_payment_ids against payment index
    for pid in settlement["linked_payment_ids"]:
        if pid in payment_index:
            result.linked_payments.append(payment_index[pid])
        else:
            result.add_error(
                LinkageError.MISSING_REFERENCE,
                f"linked_payment_id '{pid}' not found in transactions",
                field="linked_payment_ids",
                entity_id=pid,
            )

    # 2. Resolve linked_refund_ids against refund index
    for rid in settlement["linked_refund_ids"]:
        if rid in refund_index:
            result.linked_refunds.append(refund_index[rid])
        else:
            result.add_error(
                LinkageError.MISSING_REFERENCE,
                f"linked_refund_id '{rid}' not found in refunds",
                field="linked_refund_ids",
                entity_id=rid,
            )

    # 3. Link bank credit
    bc = link_bank_credit(settlement, bank_credits)
    if bc is not None:
        result.bank_credit = bc
    else:
        result.add_error(
            LinkageError.BANK_MISMATCH,
            f"No matching bank credit found for settlement {settlement['settlement_id']}",
            field="bank_credit",
            entity_id=settlement["settlement_id"],
        )

    return result


def link_entities(
    transactions: list[dict],
    settlements: list[dict],
    refunds: list[dict],
    bank_credits: list[dict],
) -> list[LinkageResult]:
    """
    Run entity linking across all settlements.

    Returns a list of LinkageResults, one per settlement.

    Also detects global linkage errors:
    - PAYMENT_OVERCLAIM
    - REFUND_OVERAGE
    - LINKAGE_MISMATCH / ORPHAN_PAYMENT
    """
    payment_index = build_payment_index(transactions)
    refund_index = build_refund_index(refunds)
    refunds_by_payment = build_refunds_by_payment_index(refunds)

    # Detect global linkage errors
    overclaim_errors = detect_payment_overclaims(settlements)
    overage_errors = detect_refund_overages(transactions, refunds)
    cross_check_errors = detect_cross_check_mismatches(transactions, settlements)

    results = []
    for settlement in settlements:
        lr = link_settlement(
            settlement,
            payment_index,
            refund_index,
            refunds_by_payment,
            bank_credits,
        )

        # Attach global errors that relate to this settlement
        sid = settlement["settlement_id"]
        pids = set(settlement["linked_payment_ids"])
        for err in overclaim_errors + overage_errors:
            if err["entity_id"] in pids:
                lr.errors.append(err)
        for err in cross_check_errors:
            # For LINKAGE_MISMATCH: match by settlement_id field
            # For ORPHAN_PAYMENT: payment points to missing settlement,
            # attach to settlement that has this payment in linked_payment_ids
            if err.get("settlement_id") == sid or err["entity_id"] in pids:
                lr.errors.append(err)

        results.append(lr)

    return results
