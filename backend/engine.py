"""
Phase 4: Deterministic Reconciliation Engine

The engine is authoritative. It never delegates math to the AI.

Checks (in order):
1. Schema & validation (handled by ingestion)
2. Duplicate detection
3. Reference existence
4. Linkage consistency
5. Fee validation (fee == floor(amount * rate) + fixed)
6. Tax validation (tax == floor(fee * 0.18))
7. Bank credit existence
8. UTR cross-check
9. Amount cross-check
10. Expected amount calculation
11. Difference calculation
12. Adjustment consistency (if adjustment declared, must bridge the gap)

Outcomes:
- CLEAN_MATCH: difference == 0, all checks pass
- DETERMINISTIC_EXCEPTION: any deterministic check fails
- MATH_DISCREPANCY: all checks pass, difference != 0
- UNPROCESSED: engine crash
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Optional

from backend.models import (
    DecisionState,
    PaymentMethod,
    ReconciliationResult,
)
from backend.linking import LinkageResult, LinkageError
from backend.evidence_packet import (
    EvidencePacketV2,
    FeeEvidence,
    TaxEvidence,
    DuplicateEvidence,
    BankCreditEvidence,
    LinkageEvidence,
)

logger = logging.getLogger("nivara.engine")


# ---------------------------------------------------------------------------
# Evidence builder: accumulates evidence fields as checks run
# ---------------------------------------------------------------------------

class _EvidenceBuilder:
    """Accumulates EvidencePacketV2 fields during reconcile_settlement."""

    __slots__ = ("settlement_id", "fee", "tax", "duplicate", "bank_credit", "linkage")

    def __init__(self, settlement_id: str) -> None:
        self.settlement_id = settlement_id
        self.fee: FeeEvidence | None = None
        self.tax: TaxEvidence | None = None
        self.duplicate: DuplicateEvidence | None = None
        self.bank_credit: BankCreditEvidence | None = None
        self.linkage: LinkageEvidence | None = None

    def build(self) -> EvidencePacketV2:
        return EvidencePacketV2(
            settlement_id=self.settlement_id,
            fee_evidence=self.fee,
            tax_evidence=self.tax,
            duplicate_evidence=self.duplicate,
            bank_credit_evidence=self.bank_credit,
            linkage_evidence=self.linkage,
        )

    def set_fee(self, computed: int, reported: int, formula: str) -> None:
        self.fee = FeeEvidence(
            computed_fee_paise=computed,
            reported_fee_paise=reported,
            formula_used=formula,
            discrepancy_paise=reported - computed,
        )

    def set_tax(self, computed: int, reported: int, rate: str) -> None:
        self.tax = TaxEvidence(
            computed_tax_paise=computed,
            reported_tax_paise=reported,
            rate_applied=rate,
            discrepancy_paise=reported - computed,
        )

    def set_duplicate(self, is_dup: bool, dup_of: str | None = None, reason: str | None = None) -> None:
        self.duplicate = DuplicateEvidence(
            is_duplicate=is_dup,
            duplicate_of=dup_of,
            duplicate_reason=reason,
        )

    def set_bank_credit(self, exists: bool, amount: int | None = None,
                        bc_utr: str | None = None, s_utr: str | None = None) -> None:
        mismatch = bc_utr is not None and s_utr is not None and bc_utr != s_utr
        self.bank_credit = BankCreditEvidence(
            bank_credit_exists=exists,
            bank_credit_amount_paise=amount,
            bank_credit_utr=bc_utr,
            settlement_utr=s_utr,
            utr_mismatch=mismatch,
        )

    def set_linkage(self, payment_ids: list[str], refund_ids: list[str]) -> None:
        self.linkage = LinkageEvidence(
            transaction_ids=payment_ids,
            refund_ids=refund_ids,
            bank_credit_ids=[],
            linkage_confidence=1.0,
        )


def _parse_date(val) -> date:
    """Parse a date from string, date, or datetime."""
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        return datetime.fromisoformat(val).date()
    return datetime.now().date()


# ---------------------------------------------------------------------------
# Fee / tax formulas (integer paise, floor division)
# ---------------------------------------------------------------------------

FEE_STRUCTURE = {
    PaymentMethod.UPI: {"rate_num": 0, "rate_den": 1, "fixed": 0},
    PaymentMethod.CARD: {"rate_num": 2, "rate_den": 100, "fixed": 100},
    PaymentMethod.NETBANKING: {"rate_num": 15, "rate_den": 1000, "fixed": 100},
}


def compute_fee(method: PaymentMethod, amount: int) -> int:
    """Compute expected fee: floor(amount * rate_num / rate_den) + fixed. Pure integer arithmetic."""
    struct = FEE_STRUCTURE[method]
    return (amount * struct["rate_num"]) // struct["rate_den"] + struct["fixed"]


def compute_tax(fee: int) -> int:
    """Compute expected tax: floor(fee * 18 / 100). Pure integer arithmetic."""
    return (fee * 18) // 100


def _make_exception(
    settlement_id: str,
    actual_amount: int,
    expected_amount: int,
    difference: int,
    checks_passed: list[str],
    checks_failed: list[str],
    evidence_packet: EvidencePacketV2 | None = None,
) -> ReconciliationResult:
    """Build a DETERMINISTIC_EXCEPTION result with correct financial values."""
    return ReconciliationResult(
        settlement_id=settlement_id,
        decision=DecisionState.DETERMINISTIC_EXCEPTION,
        difference_paise=difference,
        expected_amount_paise=expected_amount,
        actual_amount_paise=actual_amount,
        deterministic_checks_passed=checks_passed,
        deterministic_checks_failed=checks_failed,
        escalate_to_human=True,
        evidence_packet=evidence_packet,
    )


# ---------------------------------------------------------------------------
# Duplicate detection helpers
# ---------------------------------------------------------------------------

def detect_duplicates(items: list[dict], key: str, error_type: str) -> list[dict]:
    """Detect duplicate values for a key within a list of dicts."""
    seen: dict[str, int] = {}
    errors: list[dict] = []
    for i, item in enumerate(items):
        val = item.get(key)
        if val in seen:
            errors.append({
                "error_type": error_type,
                "entity_id": val,
                "message": f"Duplicate {key}: {val}",
            })
        else:
            seen[val] = i
    return errors


def detect_cross_file_utr_duplicates(
    settlements: list[dict],
    bank_credits: list[dict],
) -> list[dict]:
    """Detect duplicate UTRs within settlements and within bank_credits."""
    errors: list[dict] = []

    settlement_utrs: dict[str, int] = {}
    for i, s in enumerate(settlements):
        utr = s.get("utr")
        if utr:
            if utr in settlement_utrs:
                errors.append({
                    "error_type": "DUPLICATE_UTR",
                    "entity_id": utr,
                    "message": f"Duplicate UTR in settlements: {utr}",
                })
            else:
                settlement_utrs[utr] = i

    bank_utrs: dict[str, int] = {}
    for i, b in enumerate(bank_credits):
        utr = b.get("utr")
        if utr:
            if utr in bank_utrs:
                errors.append({
                    "error_type": "DUPLICATE_BANK_UTR",
                    "entity_id": utr,
                    "message": f"Duplicate UTR in bank_credits: {utr}",
                })
            else:
                bank_utrs[utr] = i

    return errors


# ---------------------------------------------------------------------------
# Core reconciliation
# ---------------------------------------------------------------------------

def reconcile_settlement(
    settlement: dict,
    linked_payments: list[dict],
    linked_refunds: list[dict],
    bank_credit: Optional[dict],
    linkage_errors: list[dict],
    duplicate_errors: list[dict],
    linked_bank_utr: Optional[str] = None,
) -> ReconciliationResult:
    """
    Run deterministic reconciliation for a single settlement.

    Checks run in architecture order. First failure determines outcome.
    EvidencePacketV2 is populated as checks run.
    """
    settlement_id = settlement["settlement_id"]
    actual_amount = settlement["amount"]
    checks_passed: list[str] = []
    checks_failed: list[str] = []
    ev = _EvidenceBuilder(settlement_id)

    # ── Pre-compute expected amount and difference (always visible) ──
    total_payments = sum(p["amount"] for p in linked_payments)
    total_refunds = sum(r["amount"] for r in linked_refunds)
    total_fees = sum(p["fee"] for p in linked_payments)
    total_tax = sum(p["tax"] for p in linked_payments)
    expected_amount = total_payments - total_refunds - total_fees - total_tax
    difference = actual_amount - expected_amount

    # ── Check 1: Schema & validation ──
    # Handled by ingestion; assumed valid here
    checks_passed.append("schema_validation")
    logger.info("Check schema_validation for %s: PASS", settlement_id)

    # ── Check 2: Duplicate detection ──
    settlement_utr = settlement.get("utr")
    relevant_dupes = [
        e for e in duplicate_errors
        if e.get("entity_id") in settlement["linked_payment_ids"]
        or e.get("entity_id") == settlement_id
        or (settlement_utr and e.get("entity_id") == settlement_utr)
        or (linked_bank_utr and e.get("entity_id") == linked_bank_utr)
    ]
    if relevant_dupes:
        checks_failed.append("duplicate_detection")
        ev.set_duplicate(True, str(relevant_dupes[0].get("entity_id")), str(relevant_dupes[0].get("message")))
        logger.info("Check duplicate_detection for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed, ev.build())
    else:
        checks_passed.append("duplicate_detection")
        ev.set_duplicate(False)
        logger.info("Check duplicate_detection for %s: PASS", settlement_id)

    # ── Check 3: Reference existence ──
    ref_errors = [
        e for e in linkage_errors
        if e["error_type"] == LinkageError.MISSING_REFERENCE.value
    ]
    if ref_errors:
        checks_failed.append("reference_existence")
        logger.info("Check reference_existence for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed, ev.build())
    else:
        checks_passed.append("reference_existence")
        logger.info("Check reference_existence for %s: PASS", settlement_id)

    # ── Check 4: Linkage consistency ──
    linkage_types = {
        LinkageError.LINKAGE_MISMATCH.value,
        LinkageError.ORPHAN_PAYMENT.value,
        LinkageError.PAYMENT_OVERCLAIM.value,
        LinkageError.REFUND_OVERAGE.value,
    }
    linkage_consistency_errors = [
        e for e in linkage_errors if e["error_type"] in linkage_types
    ]
    if linkage_consistency_errors:
        checks_failed.append("linkage_consistency")
        ev.set_linkage(
            [p["payment_id"] for p in linked_payments],
            [r["refund_id"] for r in linked_refunds],
        )
        logger.info("Check linkage_consistency for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed, ev.build())
    else:
        checks_passed.append("linkage_consistency")
        ev.set_linkage(
            [p["payment_id"] for p in linked_payments],
            [r["refund_id"] for r in linked_refunds],
        )
        logger.info("Check linkage_consistency for %s: PASS", settlement_id)

    # ── Check 5: Fee validation ──
    fee_mismatches: list[str] = []
    first_mismatched_payment = None
    for p in linked_payments:
        method = PaymentMethod(p["method"])
        expected_fee = compute_fee(method, p["amount"])
        if p["fee"] != expected_fee:
            fee_mismatches.append(p["payment_id"])
            if first_mismatched_payment is None:
                first_mismatched_payment = p

    if fee_mismatches:
        checks_failed.append("fee_validation")
        if first_mismatched_payment:
            method = PaymentMethod(first_mismatched_payment["method"])
            computed = compute_fee(method, first_mismatched_payment["amount"])
            formula = f"floor({first_mismatched_payment['amount']} * {FEE_STRUCTURE[method]['rate_num']}/{FEE_STRUCTURE[method]['rate_den']}) + {FEE_STRUCTURE[method]['fixed']}"
            ev.set_fee(computed, first_mismatched_payment["fee"], formula)
        logger.info("Check fee_validation for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed, ev.build())
    else:
        checks_passed.append("fee_validation")
        # Record fee evidence for first payment (pass case)
        if linked_payments:
            p0 = linked_payments[0]
            method = PaymentMethod(p0["method"])
            computed = compute_fee(method, p0["amount"])
            formula = f"floor({p0['amount']} * {FEE_STRUCTURE[method]['rate_num']}/{FEE_STRUCTURE[method]['rate_den']}) + {FEE_STRUCTURE[method]['fixed']}"
            ev.set_fee(computed, p0["fee"], formula)
        logger.info("Check fee_validation for %s: PASS", settlement_id)

    # ── Check 6: Tax validation ──
    tax_mismatches: list[str] = []
    first_mismatched_tax_payment = None
    for p in linked_payments:
        expected_tax = compute_tax(p["fee"])
        if p["tax"] != expected_tax:
            tax_mismatches.append(p["payment_id"])
            if first_mismatched_tax_payment is None:
                first_mismatched_tax_payment = p

    if tax_mismatches:
        checks_failed.append("tax_validation")
        if first_mismatched_tax_payment:
            computed = compute_tax(first_mismatched_tax_payment["fee"])
            ev.set_tax(computed, first_mismatched_tax_payment["tax"], "0.18")
        logger.info("Check tax_validation for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed, ev.build())
    else:
        checks_passed.append("tax_validation")
        if linked_payments:
            p0 = linked_payments[0]
            computed = compute_tax(p0["fee"])
            ev.set_tax(computed, p0["tax"], "0.18")
        logger.info("Check tax_validation for %s: PASS", settlement_id)

    # ── Check 7: Bank credit existence ──
    if bank_credit is None:
        checks_failed.append("bank_credit_existence")
        ev.set_bank_credit(False)
        logger.info("Check bank_credit_existence for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed, ev.build())
    else:
        checks_passed.append("bank_credit_existence")
        logger.info("Check bank_credit_existence for %s: PASS", settlement_id)

    # ── Check 8: UTR cross-check ──
    settlement_utr = settlement.get("utr")
    bank_utr = bank_credit.get("utr")
    if settlement_utr and bank_utr and settlement_utr != bank_utr:
        checks_failed.append("utr_cross_check")
        ev.set_bank_credit(True, bank_credit.get("amount"), bank_utr, settlement_utr)
        logger.info("Check utr_cross_check for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed, ev.build())
    else:
        checks_passed.append("utr_cross_check")
        logger.info("Check utr_cross_check for %s: PASS", settlement_id)

    # ── Check 9: Amount cross-check ──
    ev.set_bank_credit(True, bank_credit.get("amount"), bank_utr, settlement_utr)
    if bank_credit["amount"] != actual_amount:
        checks_failed.append("amount_cross_check")
        logger.info("Check amount_cross_check for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed, ev.build())
    else:
        checks_passed.append("amount_cross_check")
        logger.info("Check amount_cross_check for %s: PASS", settlement_id)

    # ── Check 10 & 11: Expected amount and difference ──
    checks_passed.append("expected_amount_calculation")
    logger.info("Check expected_amount_calculation for %s: PASS", settlement_id)
    checks_passed.append("difference_calculation")
    logger.info("Check difference_calculation for %s: PASS", settlement_id)

    # ── Check 12: Adjustment consistency ──
    adjustment_amount = settlement.get("adjustment_amount", 0) or 0
    if adjustment_amount != 0:
        if adjustment_amount != difference:
            checks_failed.append("adjustment_consistency")
            logger.info("Check adjustment_consistency for %s: FAIL", settlement_id)
            return ReconciliationResult(
                settlement_id=settlement_id,
                decision=DecisionState.DETERMINISTIC_EXCEPTION,
                difference_paise=difference,
                expected_amount_paise=expected_amount,
                actual_amount_paise=actual_amount,
                deterministic_checks_passed=checks_passed,
                deterministic_checks_failed=checks_failed,
                escalate_to_human=True,
                evidence_packet=ev.build(),
            )
        else:
            checks_passed.append("adjustment_consistency")
            logger.info("Check adjustment_consistency for %s: PASS", settlement_id)
    else:
        checks_passed.append("adjustment_consistency")
        logger.info("Check adjustment_consistency for %s: PASS", settlement_id)

    # ── If any check failed → DETERMINISTIC_EXCEPTION ──
    if checks_failed:
        return ReconciliationResult(
            settlement_id=settlement_id,
            decision=DecisionState.DETERMINISTIC_EXCEPTION,
            difference_paise=difference,
            expected_amount_paise=expected_amount,
            actual_amount_paise=actual_amount,
            deterministic_checks_passed=checks_passed,
            deterministic_checks_failed=checks_failed,
            escalate_to_human=True,
            evidence_packet=ev.build(),
        )

    # ── Outcome ──
    if difference == 0:
        logger.info("Final decision for %s: %s", settlement_id, DecisionState.CLEAN_MATCH.value)
        return ReconciliationResult(
            settlement_id=settlement_id,
            decision=DecisionState.CLEAN_MATCH,
            difference_paise=0,
            expected_amount_paise=expected_amount,
            actual_amount_paise=actual_amount,
            deterministic_checks_passed=checks_passed,
            deterministic_checks_failed=[],
            escalate_to_human=False,
            evidence_packet=ev.build(),
        )
    else:
        logger.info("Final decision for %s: %s", settlement_id, DecisionState.MATH_DISCREPANCY.value)
        return ReconciliationResult(
            settlement_id=settlement_id,
            decision=DecisionState.MATH_DISCREPANCY,
            difference_paise=difference,
            expected_amount_paise=expected_amount,
            actual_amount_paise=actual_amount,
            deterministic_checks_passed=checks_passed,
            deterministic_checks_failed=[],
            escalate_to_human=True,
            evidence_packet=ev.build(),
        )


# ---------------------------------------------------------------------------
# Batch engine
# ---------------------------------------------------------------------------

def run_engine(
    transactions: list[dict],
    settlements: list[dict],
    refunds: list[dict],
    bank_credits: list[dict],
    llm_client=None,
    max_workers: int = 4,
    settlement_cycle_days: int = 2,
) -> list[ReconciliationResult]:
    """
    Run deterministic reconciliation for a batch of settlements.

    1. Links entities (reuses Phase 3)
    2. Detects duplicates (reuses Phase 2 logic)
    3. Reconciles each settlement (parallel via ThreadPoolExecutor)
    4. Investigates MATH_DISCREPANCY cases with AI via fallback chain
    5. Catches crashes → UNPROCESSED

    Args:
        llm_client: If not None, AI investigation is enabled via fallback chain
            (OpenAI → Anthropic → Local). If None, AI investigation is skipped
            and non-clean settlements are escalated to human review.
        max_workers: Number of parallel workers for settlement reconciliation.
        settlement_cycle_days: Expected settlement cycle in days (default 2 for T+2).
            Use 1 for T+1 settlements.
    """
    from backend.linking import link_entities

    # Link entities
    linkage_results = link_entities(transactions, settlements, refunds, bank_credits)
    linkage_by_sid = {lr.settlement_id: lr for lr in linkage_results}

    # Duplicate detection
    dup_payment = detect_duplicates(transactions, "payment_id", "DUPLICATE_PAYMENT")
    dup_refund = detect_duplicates(refunds, "refund_id", "DUPLICATE_REFUND")
    dup_settlement = detect_duplicates(settlements, "settlement_id", "DUPLICATE_SETTLEMENT")
    dup_utr = detect_cross_file_utr_duplicates(settlements, bank_credits)
    all_duplicates = dup_payment + dup_refund + dup_settlement + dup_utr

    # Build bank credit UTR index for duplicate bank UTR detection
    bank_credit_utrs: dict[str, str] = {}
    for bc in bank_credits:
        utr = bc.get("utr")
        if utr:
            bank_credit_utrs[bc.get("settlement_id", "")] = utr

    def _reconcile_one(settlement: dict) -> ReconciliationResult:
        try:
            lr = linkage_by_sid.get(settlement["settlement_id"])
            if lr is None:
                raise ValueError(f"No linkage result for {settlement['settlement_id']}")

            # Pass linked bank credit UTR for duplicate bank UTR detection
            linked_bank_utr = lr.bank_credit.get("utr") if lr.bank_credit else None
            return reconcile_settlement(
                settlement=settlement,
                linked_payments=lr.linked_payments,
                linked_refunds=lr.linked_refunds,
                bank_credit=lr.bank_credit,
                linkage_errors=lr.errors,
                duplicate_errors=all_duplicates,
                linked_bank_utr=linked_bank_utr,
            )
        except Exception as exc:
            logger.warning("Settlement %s marked UNPROCESSED: %s", settlement["settlement_id"], exc, exc_info=True)
            return ReconciliationResult(
                settlement_id=settlement["settlement_id"],
                decision=DecisionState.UNPROCESSED,
                difference_paise=0,
                expected_amount_paise=max(1, settlement.get("amount", 1)),
                actual_amount_paise=max(1, settlement.get("amount", 1)),
                deterministic_checks_passed=[],
                deterministic_checks_failed=["engine_crash"],
                escalate_to_human=True,
            )

    results: list[ReconciliationResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_reconcile_one, s): s for s in settlements}
        for future in as_completed(futures):
            results.append(future.result())

    # Phase 7: AI investigation for MATH_DISCREPANCY cases
    from backend.ai_investigator import investigate_v2
    from backend.deterministic_guard import should_invoke_ai

    # Skip AI investigation if no LLM client provided
    if llm_client is None:
        for result in results:
            if result.decision in (DecisionState.MATH_DISCREPANCY, DecisionState.DETERMINISTIC_EXCEPTION):
                result.escalate_to_human = True
        return results

    for result in results:
        # Only invoke AI for MATH_DISCREPANCY (deterministic guard rule 1)
        if not should_invoke_ai(result.decision):
            continue

        if result.evidence_packet is None:
            logger.warning("No evidence packet for %s, marking UNRESOLVED", result.settlement_id)
            result.decision = DecisionState.UNRESOLVED
            result.escalate_to_human = True
            continue

        try:
            logger.info("Invoking AI investigation for %s", result.settlement_id)

            ai_response = investigate_v2(
                evidence_packet_v2=result.evidence_packet,
                expected_amount_paise=result.expected_amount_paise,
                actual_amount_paise=result.actual_amount_paise,
                difference_paise=result.difference_paise,
            )

            if ai_response is not None:
                result.ai_response = ai_response
                result.resolution_confidence = ai_response.raw_confidence
                result.resolution_source = "ai"
                result.ai_mode = "live"
                result.escalate_to_human = True
            else:
                # AI failed → UNRESOLVED
                result.decision = DecisionState.UNRESOLVED
                result.escalate_to_human = True
        except Exception as exc:
            logger.warning(
                "AI investigation failed for settlement %s: %s",
                result.settlement_id,
                exc,
                exc_info=True,
            )
            result.decision = DecisionState.UNRESOLVED
            result.escalate_to_human = True

    return results
