"""
Model selector: choose the right Groq model based on case complexity.

Decision rule:
- simple (1-2 evidence types present)  -> llama-3.1-8b-instant     (fast, cheap)
- complex (3+ evidence types present)  -> llama-3.1-70b-versatile  (capable)

Rationale: most MATH_DISCREPANCY cases need only two evidence kinds (e.g. fee +
tax). Routing those to the 8B instant model keeps latency low and headroom high
within free-tier rate limits. Only multi-signal investigations pay 70B latency.
"""

from __future__ import annotations

from typing import Optional

from backend.groq_client import DEFAULT_MODEL, FALLBACK_MODEL
from backend.evidence_packet import EvidencePacketV2

COMPLEXITY_SIMPLE = "simple"
COMPLEXITY_COMPLEX = "complex"

MODEL_FOR_COMPLEXITY: dict[str, str] = {
    COMPLEXITY_SIMPLE: FALLBACK_MODEL,
    COMPLEXITY_COMPLEX: DEFAULT_MODEL,
}

COMPLEXITY_THRESHOLD_TYPES = 3


def evidence_complexity(evidence_packet: Optional[EvidencePacketV2]) -> str:
    """
    Classify investigation complexity from the number of evidence types present.

    Args:
        evidence_packet: The EvidencePacketV2 populated by the engine, or None.

    Returns:
        "simple" when 1-2 evidence types are present (or packet is None),
        "complex" when 3 or more evidence types are present.
    """
    if evidence_packet is None:
        return COMPLEXITY_SIMPLE
    n_evidence_types = len(evidence_packet.get_valid_citation_ids())
    if n_evidence_types >= COMPLEXITY_THRESHOLD_TYPES:
        return COMPLEXITY_COMPLEX
    return COMPLEXITY_SIMPLE


def select_model(evidence_packet: Optional[EvidencePacketV2]) -> str:
    """
    Select the best Groq model for a given evidence packet.

    Args:
        evidence_packet: The EvidencePacketV2 populated by the engine.

    Returns:
        Groq model name (8B instant for simple, 70B versatile for complex).
    """
    complexity = evidence_complexity(evidence_packet)
    return MODEL_FOR_COMPLEXITY[complexity]


class ModelSelector:
    """Orchestration facade for evidence-complexity model selection."""

    @staticmethod
    def select(evidence_packet: Optional[EvidencePacketV2]) -> str:
        return select_model(evidence_packet)