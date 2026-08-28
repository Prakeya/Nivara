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
from typing import Any, Optional, Protocol

from backend.models import (
    AIClassification,
    AIResponse,
    EvidencePacket,
    ReconciliationResult,
    DecisionState,
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
        "Return JSON with: classification, explanation, raw_confidence, cited_evidence."
    )

    evidence_json = json.dumps({
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
    }, indent=2)

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
    )


# ---------------------------------------------------------------------------
# Mock LLM client (for testing)
# ---------------------------------------------------------------------------

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
