"""
Phase 7: AI Investigator

Classifies discrepancies using structured evidence. Never calculates.
Never approves. Never invents. All outputs → human review queue.

Usage:
    from backend.ai_investigator import investigate, MockLLMClient
    client = MockLLMClient(classification="UNEXPLAINED", confidence=0.6)
    result = investigate(evidence_packet, llm_client=client)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Protocol

from backend.models import (
    AIClassification,
    AIResponse,
    BankCreditEvidence,
    EvidencePacket,
    FeesSummary,
    LinkedPaymentsSummary,
    LinkedRefundsSummary,
    PaymentMethod,
    ReconciliationResult,
    DecisionState,
    TaxSummary,
    TimingEvidence,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# LLM Client Protocol
# ---------------------------------------------------------------------------

class LLMClient(Protocol):
    """Interface for LLM providers. Implementations must return a dict with
    classification, explanation, raw_confidence, and cited_evidence."""

    def complete(self, messages: list[dict], timeout: float = 10.0) -> dict[str, Any]:
        """Send a prompt to the LLM and return the parsed response dict.
        Raises LLMError on failure."""
        ...


# ---------------------------------------------------------------------------
# LLM error types
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Base class for LLM failures."""
    def __init__(self, error_type: str, message: str = ""):
        self.error_type = error_type
        super().__init__(message or error_type)


class LLMTimeoutError(LLMError):
    def __init__(self, message: str = "LLM timeout"):
        super().__init__("timeout", message)


class LLMAPIError(LLMError):
    def __init__(self, message: str = "LLM API error"):
        super().__init__("api_error", message)


class LLMMalformedResponseError(LLMError):
    def __init__(self, message: str = "Malformed LLM response"):
        super().__init__("malformed_json", message)


# ---------------------------------------------------------------------------
# Investigation result
# ---------------------------------------------------------------------------

@dataclass
class InvestigationResult:
    """Result of AI investigation for a single settlement."""

    settlement_id: str
    decision: DecisionState
    ai_response: Optional[AIResponse] = None
    confidence_tier: str = "LOW"
    error_type: Optional[str] = None
    escalate_to_human: bool = True
    is_mock: bool = False


# ---------------------------------------------------------------------------
# Confidence policy
# ---------------------------------------------------------------------------

def compute_confidence_tier(raw_confidence: float) -> str:
    """Map raw confidence to tier: HIGH >= 0.7, MEDIUM >= 0.4, LOW < 0.4."""
    if raw_confidence >= 0.7:
        return "HIGH"
    elif raw_confidence >= 0.4:
        return "MEDIUM"
    return "LOW"


def validate_citations(
    cited_evidence: list[str],
    evidence_packet: EvidencePacket,
) -> bool:
    """Check that all cited evidence IDs are valid.
    Valid IDs: the evidence_packet_id itself, and any of the deterministic
    checks passed/failed strings."""
    valid_ids = {
        str(evidence_packet.evidence_packet_id),
        "timing",
        "bank_credit",
        "fees_summary",
        "tax_summary",
        "linked_payments_summary",
        "linked_refunds_summary",
        "payment_details",
        "cross_settlement",
    }
    # Also accept deterministic check names
    valid_ids.update(evidence_packet.deterministic_checks_passed)
    valid_ids.update(evidence_packet.deterministic_checks_failed)

    return all(eid in valid_ids for eid in cited_evidence)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _build_prompt(evidence_packet: EvidencePacket) -> list[dict]:
    """Build the LLM prompt from an evidence packet."""
    ep = evidence_packet
    system_msg = (
        "You are a financial settlement investigator. "
        "Classify the discrepancy based on the evidence. "
        "You must cite evidence from the provided packet. "
        "Never invent information. Never calculate amounts. "
        "Return JSON with: classification, explanation, raw_confidence, cited_evidence. "
        "The explanation must include a brief remediation suggestion (what a human should investigate or do next)."
    )

    evidence_dict = {
        "evidence_packet_id": str(ep.evidence_packet_id),
        "settlement_id": ep.settlement_id,
        "expected_amount_paise": ep.expected_amount_paise,
        "actual_amount_paise": ep.actual_amount_paise,
        "difference_paise": ep.difference_paise,
        "linked_payments_summary": {
            "count": ep.linked_payments_summary.count,
            "total_paise": ep.linked_payments_summary.total_paise,
            "methods": [m.value for m in ep.linked_payments_summary.methods],
        },
        "linked_refunds_summary": {
            "count": ep.linked_refunds_summary.count,
            "total_paise": ep.linked_refunds_summary.total_paise,
        },
        "fees_summary": {
            "total_paise": ep.fees_summary.total_paise,
            "validation_result": ep.fees_summary.validation_result.value,
        },
        "tax_summary": {
            "total_paise": ep.tax_summary.total_paise,
            "validation_result": ep.tax_summary.validation_result.value,
        },
        "bank_credit": {
            "utr": ep.bank_credit.utr,
            "amount_paise": ep.bank_credit.amount_paise,
            "date": str(ep.bank_credit.date),
        },
        "timing": {
            "settlement_created_at": str(ep.timing.settlement_created_at),
            "settled_at": str(ep.timing.settled_at),
            "bank_credited_at": str(ep.timing.bank_credited_at),
            "expected_cycle_days": ep.timing.expected_cycle_days,
        },
        "deterministic_checks_passed": ep.deterministic_checks_passed,
        "deterministic_checks_failed": ep.deterministic_checks_failed,
    }

    if ep.payment_details:
        evidence_dict["payment_details"] = [
            {
                "payment_id": pd.payment_id,
                "amount_paise": pd.amount_paise,
                "method": pd.method.value,
                "fee_paise": pd.fee_paise,
                "tax_paise": pd.tax_paise,
                "fee_expected_paise": pd.fee_expected_paise,
                "tax_expected_paise": pd.tax_expected_paise,
                "fee_mismatch": pd.fee_mismatch,
                "tax_mismatch": pd.tax_mismatch,
            }
            for pd in ep.payment_details
        ]

    if ep.cross_settlement:
        evidence_dict["cross_settlement_context"] = {
            "batch_size": ep.cross_settlement.batch_size,
            "batch_fee_exception_rate": ep.cross_settlement.batch_fee_exception_rate,
            "batch_refund_rate": ep.cross_settlement.batch_refund_rate,
            "batch_math_discrepancy_rate": ep.cross_settlement.batch_math_discrepancy_rate,
            "merchant_fee_exceptions_in_batch": ep.cross_settlement.merchant_fee_exceptions_in_batch,
            "method_mix": ep.cross_settlement.method_mix,
        }

    evidence_json = json.dumps(evidence_dict, indent=2)

    user_msg = (
        f"Settlement {ep.settlement_id} has a difference of "
        f"{ep.difference_paise} paise "
        f"(expected {ep.expected_amount_paise}, actual {ep.actual_amount_paise}).\n\n"
        f"Evidence:\n{evidence_json}\n\n"
        "Classify this discrepancy. Return JSON only."
    )

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------

def _validate_response(raw: dict[str, Any]) -> AIResponse:
    """Validate and parse LLM response into AIResponse.
    Raises LLMMalformedResponseError on invalid data."""
    try:
        classification_str = raw.get("classification", "")
        classification = AIClassification(classification_str)
    except (ValueError, KeyError):
        raise LLMMalformedResponseError(
            f"Invalid classification: {raw.get('classification')}"
        )

    explanation = raw.get("explanation", "")
    if not explanation:
        raise LLMMalformedResponseError("Missing explanation")

    try:
        confidence = float(raw.get("raw_confidence", 0.0))
    except (TypeError, ValueError):
        raise LLMMalformedResponseError("Invalid confidence value")

    cited_evidence = raw.get("cited_evidence", [])
    if not isinstance(cited_evidence, list):
        raise LLMMalformedResponseError("cited_evidence must be a list")

    # Clamp confidence to [0.0, 1.0] per architecture spec
    confidence = max(0.0, min(1.0, confidence))

    return AIResponse(
        classification=classification,
        explanation=explanation,
        raw_confidence=confidence,
        cited_evidence=cited_evidence,
    )


# ---------------------------------------------------------------------------
# Core investigation
# ---------------------------------------------------------------------------

def investigate(
    evidence_packet: EvidencePacket,
    llm_client: Optional[LLMClient] = None,
    timeout: float = 10.0,
) -> InvestigationResult:
    """
    Investigate a discrepancy using the AI investigator.

    Args:
        evidence_packet: Structured evidence from the deterministic engine.
        llm_client: LLM provider. If None, returns UNRESOLVED.
        timeout: LLM timeout in seconds.

    Returns:
        InvestigationResult with decision REVIEW_REQUIRED or UNRESOLVED.
    """
    sid = evidence_packet.settlement_id

    # No LLM client → immediate UNRESOLVED
    if llm_client is None:
        return InvestigationResult(
            settlement_id=sid,
            decision=DecisionState.UNRESOLVED,
            confidence_tier="LOW",
            error_type="no_llm_client",
            escalate_to_human=True,
        )

    # Call LLM
    messages = _build_prompt(evidence_packet)
    try:
        raw_response = llm_client.complete(messages, timeout=timeout)
    except LLMTimeoutError:
        return InvestigationResult(
            settlement_id=sid,
            decision=DecisionState.UNRESOLVED,
            confidence_tier="LOW",
            error_type="timeout",
            escalate_to_human=True,
        )
    except LLMAPIError:
        return InvestigationResult(
            settlement_id=sid,
            decision=DecisionState.UNRESOLVED,
            confidence_tier="LOW",
            error_type="api_error",
            escalate_to_human=True,
        )
    except LLMError as e:
        return InvestigationResult(
            settlement_id=sid,
            decision=DecisionState.UNRESOLVED,
            confidence_tier="LOW",
            error_type=e.error_type,
            escalate_to_human=True,
        )
    except Exception:
        return InvestigationResult(
            settlement_id=sid,
            decision=DecisionState.UNRESOLVED,
            confidence_tier="LOW",
            error_type="unknown_error",
            escalate_to_human=True,
        )

    # Parse response
    try:
        ai_response = _validate_response(raw_response)
    except LLMMalformedResponseError as e:
        return InvestigationResult(
            settlement_id=sid,
            decision=DecisionState.UNRESOLVED,
            confidence_tier="LOW",
            error_type=e.error_type,
            escalate_to_human=True,
        )

    # Validate citations
    if not validate_citations(ai_response.cited_evidence, evidence_packet):
        return InvestigationResult(
            settlement_id=sid,
            decision=DecisionState.UNRESOLVED,
            ai_response=ai_response,
            confidence_tier="LOW",
            error_type="hallucinated_evidence",
            escalate_to_human=True,
        )

    # Compute confidence tier
    tier = compute_confidence_tier(ai_response.raw_confidence)

    # All AI cases → human review queue
    return InvestigationResult(
        settlement_id=sid,
        decision=DecisionState.REVIEW_REQUIRED,
        ai_response=ai_response,
        confidence_tier=tier,
        escalate_to_human=True,
        is_mock=isinstance(llm_client, DemoLLMClient),
    )


# ---------------------------------------------------------------------------
# Mock LLM client (for testing)
# ---------------------------------------------------------------------------

class OpenAIClient:
    """Real LLM client using OpenAI API. Falls back to UNRESOLVED on failure."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini"):
        try:
            from openai import OpenAI
        except ImportError:
            raise LLMError("import_error", "openai package not installed. Run: pip install openai")
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def complete(self, messages: list[dict], timeout: float = 10.0) -> dict[str, Any]:
        import json as _json
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                timeout=timeout,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            return _json.loads(content)
        except Exception as e:
            error_msg = str(e).lower()
            if "timeout" in error_msg or "timed out" in error_msg:
                raise LLMTimeoutError(str(e))
            elif "api" in error_msg or "rate" in error_msg or "auth" in error_msg:
                raise LLMAPIError(str(e))
            else:
                raise LLMError("unknown_error", str(e))


class MockLLMClient:
    """Mock LLM client that returns controlled responses for testing."""

    def __init__(
        self,
        classification: str = "UNEXPLAINED",
        explanation: str = "No clear explanation found.",
        confidence: float = 0.5,
        cited_evidence: Optional[list[str]] = None,
        fail_with: Optional[str] = None,
    ):
        self._classification = classification
        self._explanation = explanation
        self._confidence = confidence
        self._cited_evidence = cited_evidence or ["timing"]
        self._fail_with = fail_with
        self._call_count = 0

    def complete(self, messages: list[dict], timeout: float = 10.0) -> dict[str, Any]:
        self._call_count += 1

        if self._fail_with == "timeout":
            raise LLMTimeoutError()
        elif self._fail_with == "api_error":
            raise LLMAPIError()
        elif self._fail_with == "malformed_json":
            raise LLMMalformedResponseError()
        elif self._fail_with:
            raise LLMError(self._fail_with)

        return {
            "classification": self._classification,
            "explanation": self._explanation,
            "raw_confidence": self._confidence,
            "cited_evidence": self._cited_evidence,
        }


# ---------------------------------------------------------------------------
# Demo LLM client (heuristic, clearly labeled)
# ---------------------------------------------------------------------------

_DEMO_PREFIX = "[DEMO] "


def _classify_by_heuristic(evidence: EvidencePacket) -> tuple[str, str, float, list[str]]:
    """Heuristic classifier for demo without API key.

    Reasons across multiple signals: cross-settlement patterns, payment-level
    breakdowns, and batch-level statistics — not just the failed check name.

    Returns (classification, explanation, confidence, cited_evidence).
    All outputs are clearly labeled as demo to prevent confusion with live AI.
    """
    refunds = evidence.linked_refunds_summary
    timing = evidence.timing
    diff = evidence.difference_paise
    failed_checks = evidence.deterministic_checks_failed
    payments = evidence.linked_payments_summary
    cross = evidence.cross_settlement
    details = evidence.payment_details

    # ── DETERMINISTIC_EXCEPTION cases: reason across signals ──
    if failed_checks:
        check_set = set(failed_checks)

        # ── Fee + Tax both failed: systemic fee tier misconfiguration ──
        if "fee_validation" in check_set and "tax_validation" in check_set:
            mismatched = [d for d in details if d.fee_mismatch]
            methods = list(set(d.method.value for d in mismatched))
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}Systemic fee+tax failure for {evidence.settlement_id}. "
                    f"{len(mismatched)} payment(s) have incorrect fees ({', '.join(methods)} methods). "
                    f"Expected fees: {[f'{d.payment_id}: \u20B9{d.fee_expected_paise/100:.2f}' for d in mismatched[:3]]}. "
                    f"This pattern suggests a fee tier misconfiguration rather than individual errors. "
                    f"Remediation: Check merchant's contracted fee rates; verify if a fee waiver or "
                    f"promotional rate was applied inconsistently across payment methods."
                ),
                0.75,
                ["fees_summary", "tax_summary", "linked_payments_summary"],
            )

        # ── Fee validation failed: per-payment breakdown ──
        if "fee_validation" in check_set:
            mismatched = [d for d in details if d.fee_mismatch]
            overcharged = [d for d in mismatched if d.fee_paise > d.fee_expected_paise]
            undercharged = [d for d in mismatched if d.fee_paise < d.fee_expected_paise]
            batch_rate = cross.batch_fee_exception_rate if cross else 0

            if overcharged and not undercharged:
                ex = overcharged[0]
                return (
                    "UNEXPLAINED",
                    (
                        f"{_DEMO_PREFIX}Fee overcharge detected for {evidence.settlement_id}. "
                        f"{len(overcharged)} payment(s) have fees above the expected rate. "
                        f"Example: {ex.payment_id} charged \u20B9{ex.fee_paise/100:.2f}, "
                        f"expected \u20B9{ex.fee_expected_paise/100:.2f} "
                        f"({ex.method.value}). "
                        f"Batch context: {batch_rate*100:.0f}% of settlements in this batch have fee exceptions. "
                        f"Remediation: Verify if merchant is on a different fee tier; check for manual fee overrides."
                    ),
                    0.70,
                    ["fees_summary", "linked_payments_summary"],
                )
            if undercharged and not overcharged:
                ex = undercharged[0]
                return (
                    "UNEXPLAINED",
                    (
                        f"{_DEMO_PREFIX}Fee undercharge detected for {evidence.settlement_id}. "
                        f"{len(undercharged)} payment(s) have fees below the expected rate. "
                        f"Example: {ex.payment_id} charged \u20B9{ex.fee_paise/100:.2f}, "
                        f"expected \u20B9{ex.fee_expected_paise/100:.2f} "
                        f"({ex.method.value}). "
                        f"This may indicate a promotional fee waiver or rate card discrepancy. "
                        f"Remediation: Check if a fee waiver was applied; verify the merchant's contracted rate."
                    ),
                    0.70,
                    ["fees_summary", "linked_payments_summary"],
                )
            # Mixed over/under
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}Mixed fee errors for {evidence.settlement_id}. "
                    f"{len(overcharged)} overcharged, {len(undercharged)} undercharged across "
                    f"{len(mismatched)} payment(s). This pattern suggests a fee tier misalignment "
                    f"rather than isolated errors. "
                    f"Remediation: Pull the merchant's fee contract; cross-reference each payment method's "
                    f"applicable rate against what was charged."
                ),
                0.65,
                ["fees_summary", "linked_payments_summary"],
            )

        # ── Tax validation failed: per-payment derivation check ──
        if "tax_validation" in check_set:
            mismatched = [d for d in details if d.tax_mismatch]
            if mismatched:
                examples = []
                for d in mismatched[:3]:
                    examples.append(
                        f"{d.payment_id}: fee=\u20B9{d.fee_paise/100:.2f}, "
                        f"tax=\u20B9{d.tax_paise/100:.2f}, expected=\u20B9{d.tax_expected_paise/100:.2f}"
                    )
                return (
                    "UNEXPLAINED",
                    (
                        f"{_DEMO_PREFIX}Tax derivation mismatch for {evidence.settlement_id}. "
                        f"{len(mismatched)} payment(s) have incorrect GST. Examples: "
                        f"{'; '.join(examples)}. "
                        f"Rule: tax must equal floor(fee \u00d7 18/100). "
                        f"Remediation: Check if GST rate changed mid-period; verify rounding method; "
                        f"confirm tax-exclusive vs tax-inclusive handling."
                    ),
                    0.75,
                    ["tax_summary", "linked_payments_summary"],
                )
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}Tax validation failed for {evidence.settlement_id} "
                    f"but no individual payment-level mismatch found. "
                    f"Remediation: Check aggregate tax calculation across all linked payments."
                ),
                0.55,
                ["tax_summary"],
            )

        # ── Bank credit existence ──
        if "bank_credit_existence" in check_set:
            delta = (timing.bank_credited_at - timing.settled_at).days
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}No bank credit for {evidence.settlement_id} "
                    f"(expected \u20B9{evidence.expected_amount_paise/100:,.2f}). "
                    f"Settlement settled {timing.settled_at.date()}, "
                    f"bank credited {timing.bank_credited_at.date()} ({delta} day delta). "
                    f"Remediation: Check if credit is pending T+1/T+2; verify UTR mapping; "
                    f"contact bank with settlement reference."
                ),
                0.70,
                ["bank_credit", "timing"],
            )

        # ── UTR cross-check ──
        if "utr_cross_check" in check_set:
            bc = evidence.bank_credit
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}UTR mismatch for {evidence.settlement_id}. "
                    f"Settlement UTR does not match bank credit UTR ({bc.utr}). "
                    f"Bank credited \u20B9{bc.amount_paise/100:,.2f} on {bc.date}. "
                    f"Remediation: Check if UTR belongs to a different batch; "
                    f"cross-reference with bank's NEFT/RTGS reference directory."
                ),
                0.80,
                ["bank_credit", "timing"],
            )

        # ── Duplicate detection ──
        if "duplicate_detection" in check_set:
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}Duplicate detected for {evidence.settlement_id}. "
                    f"A payment ID, refund ID, or UTR appears in multiple records. "
                    f"Remediation: Identify original vs duplicate; check for retry or "
                    f"idempotency failure; remove or mark the duplicate."
                ),
                0.85,
                ["linked_payments_summary", "linked_refunds_summary"],
            )

        # ── Reference existence ──
        if "reference_existence" in check_set:
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}Missing reference for {evidence.settlement_id}. "
                    f"A linked payment or refund ID does not exist in transaction records. "
                    f"Remediation: Check if the transaction was deleted or belongs to a "
                    f"different merchant account."
                ),
                0.80,
                ["linked_payments_summary"],
            )

        # ── Linkage consistency ──
        if "linkage_consistency" in check_set:
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}Linkage inconsistency for {evidence.settlement_id}. "
                    f"A payment is claimed by multiple settlements or a refund exceeds "
                    f"its parent payment. "
                    f"Remediation: Trace each payment ID to its correct settlement."
                ),
                0.75,
                ["linked_payments_summary", "linked_refunds_summary"],
            )

        # ── Adjustment consistency ──
        if "adjustment_consistency" in check_set:
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}Adjustment inconsistency for {evidence.settlement_id}. "
                    f"Declared adjustment does not bridge expected-vs-actual gap "
                    f"(difference: \u20B9{abs(diff)/100:,.2f}). "
                    f"Remediation: Verify adjustment amount matches documented reason; "
                    f"cross-check with adjustment approval record."
                ),
                0.70,
                ["linked_payments_summary"],
            )

        # Fallback
        check = failed_checks[0]
        return (
            "UNEXPLAINED",
            (
                f"{_DEMO_PREFIX}Deterministic check '{check}' failed for {evidence.settlement_id}. "
                f"Remediation: Review the specific failed check against source records."
            ),
            0.50,
            [],
        )

    # ── MATH_DISCREPANCY cases ──

    # Refund-linked discrepancy
    if refunds.count > 0:
        return (
            "REFUND_TIMING",
            (
                f"{_DEMO_PREFIX}Linked refunds detected ({refunds.count} refunds totaling "
                f"\u20B9{refunds.total_paise/100:,.2f}). Difference of \u20B9{abs(diff)/100:,.2f} "
                f"is likely caused by refund timing within the settlement window. "
                f"Remediation: Verify refund processing dates against settlement cutoff."
            ),
            0.65,
            ["linked_refunds_summary", "timing"],
        )

    # Timing-based discrepancy
    try:
        delta = (timing.bank_credited_at - timing.settled_at).days
    except Exception:
        delta = 0

    if delta > timing.expected_cycle_days:
        return (
            "TIMING_MISMATCH",
            (
                f"{_DEMO_PREFIX}Bank credit delayed by {delta} days (expected "
                f"{timing.expected_cycle_days}-day cycle). The \u20B9{abs(diff)/100:,.2f} "
                f"difference may result from bank-side processing delay. "
                f"Remediation: Contact bank to confirm credit processing status."
            ),
            0.60,
            ["timing", "bank_credit"],
        )

    # Default
    return (
        "UNEXPLAINED",
        (
            f"{_DEMO_PREFIX}Difference of \u20B9{abs(diff)/100:,.2f} detected "
            f"but no clear timing or refund cause was found. "
            f"Remediation: Manually review the settlement breakdown."
        ),
        0.40,
        ["timing"],
    )


class DemoLLMClient:
    """Offline LLM client for demo mode. Uses deterministic heuristics
    that reason across cross-settlement patterns and payment-level breakdowns.
    Clearly labeled as DEMO to prevent confusion with live AI output.

    This client ensures the full pipeline runs end-to-end without
    requiring an API key, while making it unambiguous that outputs
    are heuristic-based, not live AI.
    """

    def complete(self, messages: list[dict], timeout: float = 10.0) -> dict[str, Any]:
        # Extract evidence packet from prompt for heuristic analysis
        evidence = self._extract_evidence(messages)
        classification, explanation, confidence, cited = _classify_by_heuristic(evidence)
        return {
            "classification": classification,
            "explanation": explanation,
            "raw_confidence": confidence,
            "cited_evidence": cited,
        }

    def _extract_evidence(self, messages: list[dict]) -> EvidencePacket:
        """Reconstruct EvidencePacket from prompt messages for heuristic."""
        import json as _json
        from backend.models import PaymentDetail, CrossSettlementContext

        for msg in messages:
            content = msg.get("content", "")
            if "Evidence:" in content:
                try:
                    evidence_start = content.index("Evidence:\n") + len("Evidence:\n")
                    evidence_end = content.index("\n\n", evidence_start)
                    evidence_json = _json.loads(content[evidence_start:evidence_end])
                    actual = max(1, evidence_json.get("actual_amount_paise", 1))
                    expected = max(0, evidence_json.get("expected_amount_paise", 0))
                    bc = evidence_json.get("bank_credit", {})
                    bc_amount = max(1, bc.get("amount_paise", 1))
                    timing = evidence_json.get("timing", {})

                    def _parse_dt(val):
                        if isinstance(val, str):
                            try:
                                return datetime.fromisoformat(val)
                            except Exception:
                                return datetime.now()
                        return datetime.now()

                    # Reconstruct payment details
                    payment_details = []
                    for pd_dict in evidence_json.get("payment_details", []):
                        payment_details.append(PaymentDetail(
                            payment_id=pd_dict.get("payment_id", ""),
                            amount_paise=pd_dict.get("amount_paise", 0),
                            method=PaymentMethod(pd_dict.get("method", "upi")),
                            fee_paise=pd_dict.get("fee_paise", 0),
                            tax_paise=pd_dict.get("tax_paise", 0),
                            fee_expected_paise=pd_dict.get("fee_expected_paise", 0),
                            tax_expected_paise=pd_dict.get("tax_expected_paise", 0),
                            fee_mismatch=pd_dict.get("fee_mismatch", False),
                            tax_mismatch=pd_dict.get("tax_mismatch", False),
                        ))

                    # Reconstruct cross-settlement context
                    cross_dict = evidence_json.get("cross_settlement_context")
                    cross_ctx = None
                    if cross_dict:
                        cross_ctx = CrossSettlementContext(
                            batch_size=cross_dict.get("batch_size", 0),
                            batch_fee_exception_rate=cross_dict.get("batch_fee_exception_rate", 0),
                            batch_refund_rate=cross_dict.get("batch_refund_rate", 0),
                            batch_math_discrepancy_rate=cross_dict.get("batch_math_discrepancy_rate", 0),
                            merchant_fee_exceptions_in_batch=cross_dict.get("merchant_fee_exceptions_in_batch", 0),
                            method_mix=cross_dict.get("method_mix", {}),
                        )

                    return EvidencePacket(
                        settlement_id=evidence_json.get("settlement_id", "UNKNOWN"),
                        expected_amount_paise=expected,
                        actual_amount_paise=actual,
                        difference_paise=evidence_json.get("difference_paise", 0),
                        linked_payments_summary=LinkedPaymentsSummary(
                            count=evidence_json.get("linked_payments_summary", {}).get("count", 0),
                            total_paise=evidence_json.get("linked_payments_summary", {}).get("total_paise", 0),
                            methods=[PaymentMethod.UPI],
                        ),
                        linked_refunds_summary=LinkedRefundsSummary(
                            count=evidence_json.get("linked_refunds_summary", {}).get("count", 0),
                            total_paise=evidence_json.get("linked_refunds_summary", {}).get("total_paise", 0),
                        ),
                        fees_summary=FeesSummary(
                            total_paise=evidence_json.get("fees_summary", {}).get("total_paise", 0),
                            structure_applied="deterministic",
                            validation_result=ValidationResult.PASSED,
                        ),
                        tax_summary=TaxSummary(
                            total_paise=evidence_json.get("tax_summary", {}).get("total_paise", 0),
                            derivation_rule="floor(fee * 0.18)",
                            validation_result=ValidationResult.PASSED,
                        ),
                        bank_credit=BankCreditEvidence(
                            utr=bc.get("utr", ""),
                            amount_paise=bc_amount,
                            date=_parse_dt(bc.get("date")).date() if bc.get("date") else datetime.now().date(),
                        ),
                        timing=TimingEvidence(
                            settlement_created_at=_parse_dt(timing.get("settlement_created_at")),
                            settled_at=_parse_dt(timing.get("settled_at")),
                            bank_credited_at=_parse_dt(timing.get("bank_credited_at")),
                            expected_cycle_days=timing.get("expected_cycle_days", 2),
                        ),
                        deterministic_checks_passed=evidence_json.get("deterministic_checks_passed", []),
                        deterministic_checks_failed=evidence_json.get("deterministic_checks_failed", []),
                        payment_details=payment_details,
                        cross_settlement=cross_ctx,
                    )
                except Exception:
                    pass

        # Fallback minimal evidence
        return EvidencePacket(
            settlement_id="UNKNOWN",
            expected_amount_paise=0,
            actual_amount_paise=1,
            difference_paise=1,
            linked_payments_summary=LinkedPaymentsSummary(count=0, total_paise=0, methods=[PaymentMethod.UPI]),
            linked_refunds_summary=LinkedRefundsSummary(count=0, total_paise=0),
            fees_summary=FeesSummary(total_paise=0, structure_applied="deterministic", validation_result=ValidationResult.PASSED),
            tax_summary=TaxSummary(total_paise=0, derivation_rule="floor(fee * 0.18)", validation_result=ValidationResult.PASSED),
            bank_credit=BankCreditEvidence(utr="", amount_paise=1, date=datetime.now().date()),
            timing=TimingEvidence(settlement_created_at=datetime.now(), settled_at=datetime.now(), bank_credited_at=datetime.now(), expected_cycle_days=2),
            deterministic_checks_passed=[],
            deterministic_checks_failed=[],
        )
