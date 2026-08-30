"""
Deterministic Guard: Enforces that deterministic decisions are FINAL.

Core rules:
1. CLEAN_MATCH and DETERMINISTIC_EXCEPTION are FINAL. No AI invocation.
2. AI is ONLY invoked for MATH_DISCREPANCY.
3. AI citations must be from EvidencePacketV2 only.
4. If AI fails → UNRESOLVED → human review. No fallback heuristic.
5. No regex, no DemoLLMClient, no rules-based fallback.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.models import DecisionState, AIResponse
from backend.evidence_packet import EvidencePacketV2

logger = logging.getLogger("nivara.guard")


class GuardViolation(Exception):
    """Raised when a deterministic guard rule is violated."""

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        self.message = message
        super().__init__(f"[{rule}] {message}")


def should_invoke_ai(decision: DecisionState) -> bool:
    """
    Rule 1: AI is ONLY invoked for MATH_DISCREPANCY.

    Args:
        decision: The deterministic engine's decision.

    Returns:
        True if AI should be invoked, False otherwise.
    """
    return decision == DecisionState.MATH_DISCREPANCY


def validate_ai_citations(
    ai_response: AIResponse,
    evidence_packet: Optional[EvidencePacketV2],
) -> list[str]:
    """
    Rule 2: AI can ONLY cite evidence IDs from EvidencePacketV2.

    Returns:
        List of invalid citation IDs (empty if all valid).
    """
    if evidence_packet is None:
        return list(ai_response.cited_evidence)

    valid_ids = evidence_packet.get_valid_citation_ids()
    return [cid for cid in ai_response.cited_evidence if cid not in valid_ids]


def validate_ai_response(ai_response: AIResponse) -> list[str]:
    """
    Rule 3: AI response must have all required fields populated.

    Returns:
        List of validation error messages (empty if valid).
    """
    errors: list[str] = []

    if not ai_response.explanation or not ai_response.explanation.strip():
        errors.append("AI response explanation is empty")

    if not ai_response.cited_evidence:
        errors.append("AI response has no cited evidence")

    if ai_response.raw_confidence < 0.0 or ai_response.raw_confidence > 1.0:
        errors.append(f"AI confidence {ai_response.raw_confidence} out of range [0.0, 1.0]")

    return errors


def apply_guard(
    decision: DecisionState,
    ai_response: Optional[AIResponse] = None,
    evidence_packet: Optional[EvidencePacketV2] = None,
) -> DecisionState:
    """
    Apply deterministic guard rules and return final decision.

    - CLEAN_MATCH / DETERMINISTIC_EXCEPTION: returned as-is (AI never runs).
    - MATH_DISCREPANCY with valid AI response: use AI classification.
    - MATH_DISCREPANCY with invalid AI response or no AI: UNRESOLVED.

    Returns:
        Final decision after guard validation.
    """
    # Rule 1: Deterministic decisions are final
    if not should_invoke_ai(decision):
        logger.info("Guard: decision %s is final, no AI invoked", decision.value)
        return decision

    # At this point, decision is MATH_DISCREPANCY
    if ai_response is None:
        logger.warning("Guard: MATH_DISCREPANCY but no AI response, returning UNRESOLVED")
        return DecisionState.UNRESOLVED

    # Rule 3: Validate AI response fields
    response_errors = validate_ai_response(ai_response)
    if response_errors:
        logger.warning("Guard: AI response validation failed: %s", response_errors)
        return DecisionState.UNRESOLVED

    # Rule 2: Validate citations
    invalid_citations = validate_ai_citations(ai_response, evidence_packet)
    if invalid_citations:
        logger.warning("Guard: AI cited invalid evidence: %s", invalid_citations)
        return DecisionState.UNRESOLVED

    # AI response is valid — return the decision
    # The AI doesn't change the decision; it provides explanation and confidence
    logger.info("Guard: AI response validated, keeping MATH_DISCREPANCY decision")
    return decision
