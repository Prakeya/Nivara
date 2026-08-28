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

Outcomes:
- CLEAN_MATCH: difference == 0, all checks pass
- DETERMINISTIC_EXCEPTION: any deterministic check fails
- MATH_DISCREPANCY: all checks pass, difference != 0
- UNPROCESSED: engine crash
"""

from typing import Optional

from backend.models import (
    DecisionState,
    PaymentMethod,
    ReconciliationResult,
)
from backend.linking import LinkageResult, LinkageError


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
    checks_passed: list[str],
    checks_failed: list[str],
) -> ReconciliationResult:
    """Build a DETERMINISTIC_EXCEPTION result."""
    return ReconciliationResult(
        settlement_id=settlement_id,
        decision=DecisionState.DETERMINISTIC_EXCEPTION,
        difference_paise=0,
        expected_amount_paise=actual_amount,
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
) -> ReconciliationResult:
    """
    Run deterministic reconciliation for a single settlement.

    Checks run in architecture order. First failure determines outcome.
    """
    settlement_id = settlement["settlement_id"]
    actual_amount = settlement["amount"]
    checks_passed: list[str] = []
    checks_failed: list[str] = []

    # ── Check 1: Schema & validation ──
    # Handled by ingestion; assumed valid here
    checks_passed.append("schema_validation")

    # ── Check 2: Duplicate detection ──
    relevant_dupes = [
        e for e in duplicate_errors
        if e.get("entity_id") in settlement["linked_payment_ids"]
        or e.get("entity_id") == settlement_id
    ]
    if relevant_dupes:
        checks_failed.append("duplicate_detection")
        return _make_exception(settlement_id, actual_amount, checks_passed, checks_failed)
    else:
        checks_passed.append("duplicate_detection")

    # ── Check 3: Reference existence ──
    ref_errors = [
        e for e in linkage_errors
        if e["error_type"] == LinkageError.MISSING_REFERENCE.value
    ]
    if ref_errors:
        checks_failed.append("reference_existence")
        return _make_exception(settlement_id, actual_amount, checks_passed, checks_failed)
    else:
        checks_passed.append("reference_existence")

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
        return _make_exception(settlement_id, actual_amount, checks_passed, checks_failed)
    else:
        checks_passed.append("linkage_consistency")

    # ── Check 5: Fee validation ──
    fee_mismatches: list[str] = []
    for p in linked_payments:
        method = PaymentMethod(p["method"])
        expected_fee = compute_fee(method, p["amount"])
        if p["fee"] != expected_fee:
            fee_mismatches.append(p["payment_id"])

    if fee_mismatches:
        checks_failed.append("fee_validation")
        return _make_exception(settlement_id, actual_amount, checks_passed, checks_failed)
    else:
        checks_passed.append("fee_validation")

    # ── Check 6: Tax validation ──
    tax_mismatches: list[str] = []
    for p in linked_payments:
        expected_tax = compute_tax(p["fee"])
        if p["tax"] != expected_tax:
            tax_mismatches.append(p["payment_id"])

    if tax_mismatches:
        checks_failed.append("tax_validation")
        return _make_exception(settlement_id, actual_amount, checks_passed, checks_failed)
    else:
        checks_passed.append("tax_validation")

    # ── Check 7: Bank credit existence ──
    if bank_credit is None:
        checks_failed.append("bank_credit_existence")
        return _make_exception(settlement_id, actual_amount, checks_passed, checks_failed)
    else:
        checks_passed.append("bank_credit_existence")

    # ── Check 8: UTR cross-check ──
    settlement_utr = settlement.get("utr")
    bank_utr = bank_credit.get("utr")
    if settlement_utr and bank_utr and settlement_utr != bank_utr:
        checks_failed.append("utr_cross_check")
        return _make_exception(settlement_id, actual_amount, checks_passed, checks_failed)
    else:
        checks_passed.append("utr_cross_check")

    # ── Check 9: Amount cross-check ──
    if bank_credit["amount"] != actual_amount:
        checks_failed.append("amount_cross_check")
        return _make_exception(settlement_id, actual_amount, checks_passed, checks_failed)
    else:
        checks_passed.append("amount_cross_check")

    # ── Check 10 & 11: Expected amount and difference ──
    # Always compute expected amount (model requires difference == actual - expected)
    total_payments = sum(p["amount"] for p in linked_payments)
    total_refunds = sum(r["amount"] for r in linked_refunds)
    total_fees = sum(p["fee"] for p in linked_payments)
    total_tax = sum(p["tax"] for p in linked_payments)

    expected_amount = total_payments - total_refunds - total_fees - total_tax
    difference = actual_amount - expected_amount

    checks_passed.append("expected_amount_calculation")
    checks_passed.append("difference_calculation")

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
) -> list[ReconciliationResult]:
    """
    Run deterministic reconciliation for a batch of settlements.

    1. Links entities (reuses Phase 3)
    2. Detects duplicates (reuses Phase 2 logic)
    3. Reconciles each settlement
    4. Catches crashes → UNPROCESSED
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

    results: list[ReconciliationResult] = []
    for settlement in settlements:
        try:
            lr = linkage_by_sid.get(settlement["settlement_id"])
            if lr is None:
                raise ValueError(f"No linkage result for {settlement['settlement_id']}")

            result = reconcile_settlement(
                settlement=settlement,
                linked_payments=lr.linked_payments,
                linked_refunds=lr.linked_refunds,
                bank_credit=lr.bank_credit,
                linkage_errors=lr.errors,
                duplicate_errors=all_duplicates,
            )
            results.append(result)
        except Exception:
            results.append(ReconciliationResult(
                settlement_id=settlement["settlement_id"],
                decision=DecisionState.UNPROCESSED,
                difference_paise=0,
                expected_amount_paise=settlement["amount"],
                actual_amount_paise=settlement["amount"],
                deterministic_checks_passed=[],
                deterministic_checks_failed=["engine_crash"],
                escalate_to_human=True,
            ))

    # Phase 7: AI investigation for MATH_DISCREPANCY cases
    from backend.ai_investigator import investigate, MockLLMClient
    from backend.models import (
        EvidencePacket, LinkedPaymentsSummary, LinkedRefundsSummary,
        FeesSummary, TaxSummary, BankCreditEvidence, TimingEvidence,
        PaymentMethod, ValidationResult,
    )
    from datetime import datetime

    # Use mock LLM for demo (returns controlled responses)
    mock_llm = MockLLMClient()

    for result in results:
        if result.decision == DecisionState.MATH_DISCREPANCY:
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
                            validation_result=ValidationResult.PASSED,
                        ),
                        tax_summary=TaxSummary(
                            total_paise=sum(p.get("tax", 0) for p in linked_payments),
                            derivation_rule="floor(fee * 0.18)",
                            validation_result=ValidationResult.PASSED,
                        ),
                        bank_credit=BankCreditEvidence(
                            utr=bank_credit.get("utr", "") if bank_credit else "",
                            amount_paise=bank_credit["amount"] if bank_credit else 0,
                            date=bank_credit.get("date", datetime.now().date()) if bank_credit else datetime.now().date(),
                        ),
                        timing=TimingEvidence(
                            settlement_created_at=datetime.fromisoformat(settlement["created_at"]) if isinstance(settlement.get("created_at"), str) else datetime.now(),
                            settled_at=datetime.fromisoformat(settlement["settled_at"]) if isinstance(settlement.get("settled_at"), str) else datetime.now(),
                            bank_credited_at=datetime.now(),
                            expected_cycle_days=2,
                        ),
                        deterministic_checks_passed=result.deterministic_checks_passed,
                        deterministic_checks_failed=result.deterministic_checks_failed,
                    )

                    ai_result = investigate(evidence, llm_client=mock_llm)
                    if ai_result.ai_response is not None:
                        result.ai_response = ai_result.ai_response
                        # Update decision based on AI investigation
                        result.decision = ai_result.decision
                        result.escalate_to_human = True
                except Exception:
                    pass  # AI failure → human review (safety invariant)

    return results
