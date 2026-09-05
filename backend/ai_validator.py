"""
AI Validator: Validates AI responses against EvidencePacketV2.

Core responsibilities:
1. Parse LLM JSON response into AIResponse
2. Validate all cited evidence IDs exist in EvidencePacketV2
3. Track token usage and cost per call
4. Return None on any failure (no heuristic fallback)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.models import (
    AIClassification,
    AIRecommendedAction,
    AIResponse,
    DecisionState,
)
from backend.evidence_packet import EvidencePacketV2

logger = logging.getLogger("nivara.ai_validator")


@dataclass
class AICallMetrics:
    """Tracks metrics for a single AI call."""

    provider: str = "unknown"
    tokens_in: int = 0
    tokens_out: int = 0
    cost_inr: float = 0.0
    latency_ms: int = 0
    prompt_version: str = "unknown"


@dataclass
class AIValidationResult:
    """Result of AI validation."""

    ai_response: Optional[AIResponse] = None
    metrics: AICallMetrics = field(default_factory=AICallMetrics)
    error: Optional[str] = None


@dataclass(frozen=True)
class ValidationResult:
    """Small orchestration-facing validation result."""

    is_valid: bool
    violations: list[str] = field(default_factory=list)


class AIValidator:
    """Validate an already parsed AI response against its evidence packet."""

    @staticmethod
    def validate(
        ai_response: Optional[AIResponse],
        evidence_packet: Optional[EvidencePacketV2],
        expected_paise: int = 0,
        actual_paise: int = 0,
    ) -> ValidationResult:
        del expected_paise, actual_paise
        if ai_response is None:
            return ValidationResult(False, ["AI response is missing"])
        from backend.deterministic_guard import (
            validate_ai_citations as validate_guard_citations,
            validate_ai_response as validate_guard_response,
        )

        violations = validate_guard_response(ai_response)
        if evidence_packet is None:
            violations.append("Evidence packet is missing")
        else:
            violations.extend(validate_guard_citations(ai_response, evidence_packet))
        return ValidationResult(not violations, violations)


# Provider cost per 1K tokens (INR)
PROVIDER_COST_PER_1K = {
    "openai": 0.002,
    "anthropic": 0.003,
    "local": 0.0,
    "groq": 0.0,  # free tier
}

# Valid AIClassification values
_VALID_CLASSIFICATIONS = {c.value for c in AIClassification}


def parse_ai_response(raw_json: dict[str, Any]) -> Optional[AIResponse]:
    """
    Parse a raw JSON dict from LLM into an AIResponse.

    Returns None if parsing fails or required fields are missing.
    """
    try:
        classification_str = raw_json.get("classification", "")
        if classification_str not in _VALID_CLASSIFICATIONS:
            logger.warning("Invalid AI classification: %s", classification_str)
            return None

        explanation = raw_json.get("explanation", "").strip()
        if not explanation:
            logger.warning("AI response has empty explanation")
            return None

        confidence = float(raw_json.get("confidence", 0.0))
        if not (0.0 <= confidence <= 1.0):
            logger.warning("AI confidence out of range: %s", confidence)
            return None

        cited_evidence = raw_json.get("cited_evidence", [])
        if not isinstance(cited_evidence, list):
            logger.warning("cited_evidence is not a list")
            return None

        return AIResponse(
            classification=AIClassification(classification_str),
            explanation=explanation,
            raw_confidence=confidence,
            cited_evidence=cited_evidence,
        )
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("Failed to parse AI response: %s", exc)
        return None


def validate_citations(
    ai_response: AIResponse,
    evidence_packet: EvidencePacketV2,
) -> list[str]:
    """
    Validate that all cited evidence IDs exist in EvidencePacketV2.

    Returns:
        List of invalid citation IDs (empty if all valid).
    """
    valid_ids = evidence_packet.get_valid_citation_ids()
    return [cid for cid in ai_response.cited_evidence if cid not in valid_ids]


def compute_cost(provider: str, tokens_in: int, tokens_out: int) -> float:
    """Compute INR cost for a given provider and token counts."""
    rate = PROVIDER_COST_PER_1K.get(provider, 0.002)
    total_tokens = tokens_in + tokens_out
    return (total_tokens / 1000.0) * rate


def validate_ai_response(
    raw_response: dict[str, Any],
    evidence_packet: EvidencePacketV2,
    provider: str = "unknown",
    prompt_version: str = "unknown",
    latency_ms: int = 0,
    model_name: str = "unknown",
) -> AIValidationResult:
    """
    Full AI validation pipeline: parse → validate citations → return result.

    Returns AIValidationResult with ai_response=None if any step fails.
    """
    metrics = AICallMetrics(
        provider=provider,
        tokens_in=raw_response.get("usage", {}).get("prompt_tokens", 0),
        tokens_out=raw_response.get("usage", {}).get("completion_tokens", 0),
        latency_ms=latency_ms,
        prompt_version=prompt_version,
    )
    metrics.cost_inr = compute_cost(provider, metrics.tokens_in, metrics.tokens_out)

    # Parse response
    ai_response = parse_ai_response(raw_response)
    if ai_response is None:
        return AIValidationResult(metrics=metrics, error="Failed to parse AI response")

    # Attach metrics to AIResponse
    ai_response.prompt_version = prompt_version
    ai_response.tokens_in = metrics.tokens_in
    ai_response.tokens_out = metrics.tokens_out
    ai_response.provider_name = provider
    ai_response.model_name = model_name
    ai_response.cost_inr = metrics.cost_inr
    ai_response.latency_ms = latency_ms

    # Validate citations
    invalid = validate_citations(ai_response, evidence_packet)
    if invalid:
        logger.warning("AI cited invalid evidence: %s", invalid)
        return AIValidationResult(
            metrics=metrics,
            error=f"Invalid citations: {invalid}",
        )

    return AIValidationResult(ai_response=ai_response, metrics=metrics)
