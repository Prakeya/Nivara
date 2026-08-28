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
from typing import Optional

from backend.models import (
    DecisionState,
    PaymentMethod,
    ReconciliationResult,
)
from backend.linking import LinkageResult, LinkageError

logger = logging.getLogger("nivara.engine")


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
    """
    settlement_id = settlement["settlement_id"]
    actual_amount = settlement["amount"]
    checks_passed: list[str] = []
    checks_failed: list[str] = []

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
        logger.info("Check duplicate_detection for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed)
    else:
        checks_passed.append("duplicate_detection")
        logger.info("Check duplicate_detection for %s: PASS", settlement_id)

    # ── Check 3: Reference existence ──
    ref_errors = [
        e for e in linkage_errors
        if e["error_type"] == LinkageError.MISSING_REFERENCE.value
    ]
    if ref_errors:
        checks_failed.append("reference_existence")
        logger.info("Check reference_existence for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed)
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
        logger.info("Check linkage_consistency for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed)
    else:
        checks_passed.append("linkage_consistency")
        logger.info("Check linkage_consistency for %s: PASS", settlement_id)

    # ── Check 5: Fee validation ──
    fee_mismatches: list[str] = []
    for p in linked_payments:
        method = PaymentMethod(p["method"])
        expected_fee = compute_fee(method, p["amount"])
        if p["fee"] != expected_fee:
            fee_mismatches.append(p["payment_id"])

    if fee_mismatches:
        checks_failed.append("fee_validation")
        logger.info("Check fee_validation for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed)
    else:
        checks_passed.append("fee_validation")
        logger.info("Check fee_validation for %s: PASS", settlement_id)

    # ── Check 6: Tax validation ──
    tax_mismatches: list[str] = []
    for p in linked_payments:
        expected_tax = compute_tax(p["fee"])
        if p["tax"] != expected_tax:
            tax_mismatches.append(p["payment_id"])

    if tax_mismatches:
        checks_failed.append("tax_validation")
        logger.info("Check tax_validation for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed)
    else:
        checks_passed.append("tax_validation")
        logger.info("Check tax_validation for %s: PASS", settlement_id)

    # ── Check 7: Bank credit existence ──
    if bank_credit is None:
        checks_failed.append("bank_credit_existence")
        logger.info("Check bank_credit_existence for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed)
    else:
        checks_passed.append("bank_credit_existence")
        logger.info("Check bank_credit_existence for %s: PASS", settlement_id)

    # ── Check 8: UTR cross-check ──
    settlement_utr = settlement.get("utr")
    bank_utr = bank_credit.get("utr")
    if settlement_utr and bank_utr and settlement_utr != bank_utr:
        checks_failed.append("utr_cross_check")
        logger.info("Check utr_cross_check for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed)
    else:
        checks_passed.append("utr_cross_check")
        logger.info("Check utr_cross_check for %s: PASS", settlement_id)

    # ── Check 9: Amount cross-check ──
    if bank_credit["amount"] != actual_amount:
        checks_failed.append("amount_cross_check")
        logger.info("Check amount_cross_check for %s: FAIL", settlement_id)
        return _make_exception(settlement_id, actual_amount, expected_amount, difference, checks_passed, checks_failed)
    else:
        checks_passed.append("amount_cross_check")
        logger.info("Check amount_cross_check for %s: PASS", settlement_id)

    # ── Check 10 & 11: Expected amount and difference ──
    # Expected amount and difference are pre-computed at the top of this function
    # so they are always visible even when earlier checks fail (DETERMINISTIC_EXCEPTION).
    checks_passed.append("expected_amount_calculation")
    logger.info("Check expected_amount_calculation for %s: PASS", settlement_id)
    checks_passed.append("difference_calculation")
    logger.info("Check difference_calculation for %s: PASS", settlement_id)

    # ── Check 12: Adjustment consistency ──
    # If the settlement declares an adjustment amount, verify it accounts for
    # the difference between actual and expected. Adjustments are legitimate
    # finance ops corrections (chargebacks, manual adjustments) that must be
    # explicitly declared — silent mismatches are never acceptable.
    adjustment_amount = settlement.get("adjustment_amount", 0) or 0
    if adjustment_amount != 0:
        # Adjustment should bridge the gap: actual == expected + adjustment
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
) -> list[ReconciliationResult]:
    """
    Run deterministic reconciliation for a batch of settlements.

    1. Links entities (reuses Phase 3)
    2. Detects duplicates (reuses Phase 2 logic)
    3. Reconciles each settlement
    4. Investigates MATH_DISCREPANCY and DETERMINISTIC_EXCEPTION cases with AI
    5. Catches crashes → UNPROCESSED

    Args:
        llm_client: LLM provider for AI investigation. If None, falls back to
            DemoLLMClient (deterministic heuristic classifier). In production,
            pass OpenAIClient; in tests, pass MockLLMClient.
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

    results: list[ReconciliationResult] = []
    for settlement in settlements:
        try:
            lr = linkage_by_sid.get(settlement["settlement_id"])
            if lr is None:
                raise ValueError(f"No linkage result for {settlement['settlement_id']}")

            # Pass linked bank credit UTR for duplicate bank UTR detection
            linked_bank_utr = lr.bank_credit.get("utr") if lr.bank_credit else None
            result = reconcile_settlement(
                settlement=settlement,
                linked_payments=lr.linked_payments,
                linked_refunds=lr.linked_refunds,
                bank_credit=lr.bank_credit,
                linkage_errors=lr.errors,
                duplicate_errors=all_duplicates,
                linked_bank_utr=linked_bank_utr,
            )
            results.append(result)
        except Exception as exc:
            logger.warning("Settlement %s marked UNPROCESSED: %s", settlement["settlement_id"], exc, exc_info=True)
            results.append(ReconciliationResult(
                settlement_id=settlement["settlement_id"],
                decision=DecisionState.UNPROCESSED,
                difference_paise=0,
                expected_amount_paise=max(1, settlement.get("amount", 1)),
                actual_amount_paise=max(1, settlement.get("amount", 1)),
                deterministic_checks_passed=[],
                deterministic_checks_failed=["engine_crash"],
                escalate_to_human=True,
            ))

    # Phase 7: AI investigation for MATH_DISCREPANCY and DETERMINISTIC_EXCEPTION cases
    from backend.ai_investigator import investigate, DemoLLMClient
    from backend.models import (
        EvidencePacket, LinkedPaymentsSummary, LinkedRefundsSummary,
        FeesSummary, TaxSummary, BankCreditEvidence, TimingEvidence,
        PaymentMethod, ValidationResult, PaymentDetail, CrossSettlementContext,
    )
    from datetime import datetime

    effective_llm_client = llm_client if llm_client is not None else DemoLLMClient()

    # Build cross-settlement statistics (data the per-settlement engine cannot see)
    total_results = len(results)
    fee_exception_count = sum(
        1 for r in results if "fee_validation" in r.deterministic_checks_failed
    )
    refund_count = sum(
        1 for r in results
        if r.decision in (DecisionState.MATH_DISCREPANCY, DecisionState.REVIEW_REQUIRED)
        and r.ai_response and r.ai_response.classification.value == "REFUND_TIMING"
    )
    math_disc_count = sum(
        1 for r in results if r.decision == DecisionState.MATH_DISCREPANCY
    )
    batch_fee_exception_rate = fee_exception_count / total_results if total_results else 0
    batch_refund_rate = refund_count / total_results if total_results else 0
    batch_math_disc_rate = math_disc_count / total_results if total_results else 0

    # Method mix across entire batch
    method_counter: dict[str, int] = {}
    for settlement in settlements:
        lr = linkage_by_sid.get(settlement["settlement_id"])
        if lr:
            for p in lr.linked_payments:
                m = p.get("method", "unknown")
                method_counter[m] = method_counter.get(m, 0) + 1

    for result in results:
        if result.decision in (DecisionState.MATH_DISCREPANCY, DecisionState.DETERMINISTIC_EXCEPTION):
            settlement = next(
                (s for s in settlements if s["settlement_id"] == result.settlement_id),
                None,
            )
            if settlement is not None:
                lr = linkage_by_sid.get(result.settlement_id)
                try:
                    # Build evidence packet
                    linked_payments = lr.linked_payments if lr else []
                    linked_refunds = lr.linked_refunds if lr else []
                    bank_credit = lr.bank_credit if lr else None

                    methods = list(set(PaymentMethod(p["method"]) for p in linked_payments if "method" in p))

                    # Determine fee/tax validation status based on which checks failed
                    fee_failed = "fee_validation" in result.deterministic_checks_failed
                    tax_failed = "tax_validation" in result.deterministic_checks_failed

                    # Build payment-level breakdowns (data the aggregate engine discards)
                    payment_details = []
                    for p in linked_payments:
                        method = PaymentMethod(p["method"])
                        fee_exp = compute_fee(method, p["amount"])
                        tax_exp = compute_tax(p["fee"])
                        payment_details.append(PaymentDetail(
                            payment_id=p["payment_id"],
                            amount_paise=p["amount"],
                            method=method,
                            fee_paise=p.get("fee", 0),
                            tax_paise=p.get("tax", 0),
                            fee_expected_paise=fee_exp,
                            tax_expected_paise=tax_exp,
                            fee_mismatch=(p.get("fee", 0) != fee_exp),
                            tax_mismatch=(p.get("tax", 0) != tax_exp),
                        ))

                    # Cross-settlement context (patterns across the batch)
                    cross_ctx = CrossSettlementContext(
                        batch_size=total_results,
                        batch_fee_exception_rate=batch_fee_exception_rate,
                        batch_refund_rate=batch_refund_rate,
                        batch_math_discrepancy_rate=batch_math_disc_rate,
                        merchant_fee_exceptions_in_batch=fee_exception_count,
                        method_mix=method_counter,
                    )

                    evidence = EvidencePacket(
                        settlement_id=result.settlement_id,
                        expected_amount_paise=result.expected_amount_paise,
                        actual_amount_paise=result.actual_amount_paise,
                        difference_paise=result.difference_paise,
                        linked_payments_summary=LinkedPaymentsSummary(
                            count=len(linked_payments),
                            total_paise=sum(p["amount"] for p in linked_payments),
                            methods=methods,
                        ),
                        linked_refunds_summary=LinkedRefundsSummary(
                            count=len(linked_refunds),
                            total_paise=sum(r["amount"] for r in linked_refunds),
                        ),
                        fees_summary=FeesSummary(
                            total_paise=sum(p.get("fee", 0) for p in linked_payments),
                            structure_applied="deterministic",
                            validation_result=ValidationResult.FAILED if fee_failed else ValidationResult.PASSED,
                        ),
                        tax_summary=TaxSummary(
                            total_paise=sum(p.get("tax", 0) for p in linked_payments),
                            derivation_rule="floor(fee * 0.18)",
                            validation_result=ValidationResult.FAILED if tax_failed else ValidationResult.PASSED,
                        ),
                        bank_credit=BankCreditEvidence(
                            utr=bank_credit.get("utr", "") if bank_credit else "",
                            amount_paise=bank_credit["amount"] if bank_credit else 0,
                            date=bank_credit.get("date", datetime.now().date()) if bank_credit else datetime.now().date(),
                        ),
                        timing=TimingEvidence(
                            settlement_created_at=datetime.fromisoformat(settlement["created_at"]) if isinstance(settlement.get("created_at"), str) else datetime.now(),
                            settled_at=datetime.fromisoformat(settlement["settled_at"]) if isinstance(settlement.get("settled_at"), str) else datetime.now(),
                            bank_credited_at=datetime.combine(bank_credit["date"], datetime.min.time()) if bank_credit and "date" in bank_credit else datetime.now(),
                            expected_cycle_days=2,
                        ),
                        deterministic_checks_passed=result.deterministic_checks_passed,
                        deterministic_checks_failed=result.deterministic_checks_failed,
                        payment_details=payment_details,
                        cross_settlement=cross_ctx,
                    )

                    logger.info("Invoking exception analysis for %s", result.settlement_id)
                    ai_result = investigate(evidence, llm_client=effective_llm_client)
                    if ai_result.ai_response is not None:
                        result.ai_response = ai_result.ai_response
                        result.decision = ai_result.decision
                        result.escalate_to_human = True
                        result.ai_mode = "demo" if ai_result.is_mock else "live"
                    else:
                        # LLM failed → UNRESOLVED, keep deterministic result otherwise
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
