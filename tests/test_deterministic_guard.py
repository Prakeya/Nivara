"""
Tests for DeterministicGuard — enforces that deterministic decisions are FINAL.

Covers:
- should_invoke_ai: only MATH_DISCREPANCY
- validate_ai_citations: only EvidencePacketV2 IDs
- validate_ai_response: required fields
- apply_guard: end-to-end guard logic
"""

from __future__ import annotations

import pytest

from backend.models import DecisionState, AIResponse, AIClassification, AIRecommendedAction
from backend.evidence_packet import EvidencePacketV2
from backend.deterministic_guard import (
    should_invoke_ai,
    validate_ai_citations,
    validate_ai_response,
    apply_guard,
    GuardViolation,
)


def _make_ai_response(
    explanation: str = "Fee mismatch of 50 paise",
    cited_evidence: list[str] | None = None,
    confidence: float = 0.85,
) -> AIResponse:
    return AIResponse(
        classification=AIClassification.TIMING_MISMATCH,
        explanation=explanation,
        raw_confidence=confidence,
        cited_evidence=["fee_evidence"] if cited_evidence is None else cited_evidence,
    )


def _make_evidence_packet() -> EvidencePacketV2:
    from tests.test_evidence_packet import _make_full_packet
    return _make_full_packet()


# ---------------------------------------------------------------------------
# Tests: should_invoke_ai
# ---------------------------------------------------------------------------


class TestShouldInvokeAi:
    def test_clean_match_no_ai(self) -> None:
        assert should_invoke_ai(DecisionState.CLEAN_MATCH) is False

    def test_deterministic_exception_no_ai(self) -> None:
        assert should_invoke_ai(DecisionState.DETERMINISTIC_EXCEPTION) is False

    def test_math_discrepancy_invokes_ai(self) -> None:
        assert should_invoke_ai(DecisionState.MATH_DISCREPANCY) is True

    def test_unprocessed_no_ai(self) -> None:
        assert should_invoke_ai(DecisionState.UNPROCESSED) is False


# ---------------------------------------------------------------------------
# Tests: validate_ai_citations
# ---------------------------------------------------------------------------


class TestValidateAiCitations:
    def test_all_citations_valid(self) -> None:
        ai = _make_ai_response(cited_evidence=["fee_evidence", "tax_evidence"])
        packet = _make_evidence_packet()
        invalid = validate_ai_citations(ai, packet)
        assert invalid == []

    def test_invalid_citation(self) -> None:
        ai = _make_ai_response(cited_evidence=["fee_evidence", "nonexistent"])
        packet = _make_evidence_packet()
        invalid = validate_ai_citations(ai, packet)
        assert invalid == ["nonexistent"]

    def test_no_evidence_packet_all_invalid(self) -> None:
        ai = _make_ai_response(cited_evidence=["fee_evidence"])
        invalid = validate_ai_citations(ai, None)
        assert invalid == ["fee_evidence"]

    def test_empty_citations(self) -> None:
        ai = _make_ai_response(cited_evidence=[])
        packet = _make_evidence_packet()
        invalid = validate_ai_citations(ai, packet)
        assert invalid == []


# ---------------------------------------------------------------------------
# Tests: validate_ai_response
# ---------------------------------------------------------------------------


class TestValidateAiResponse:
    def test_valid_response(self) -> None:
        ai = _make_ai_response()
        errors = validate_ai_response(ai)
        assert errors == []

    def test_empty_explanation(self) -> None:
        ai = _make_ai_response(explanation="  ")
        errors = validate_ai_response(ai)
        assert len(errors) == 1
        assert "explanation" in errors[0]

    def test_no_cited_evidence(self) -> None:
        ai = _make_ai_response(cited_evidence=[])
        errors = validate_ai_response(ai)
        assert len(errors) == 1
        assert "cited evidence" in errors[0]

    def test_confidence_validation_exists(self) -> None:
        # Pydantic enforces 0.0-1.0 range via validate_assignment
        # validate_ai_response also checks the range as a defense-in-depth layer
        ai = _make_ai_response(confidence=0.5)
        errors = validate_ai_response(ai)
        assert errors == []
        # Pydantic prevents setting raw_confidence > 1.0:
        with pytest.raises(Exception):
            ai.raw_confidence = 1.5


# ---------------------------------------------------------------------------
# Tests: apply_guard
# ---------------------------------------------------------------------------


class TestApplyGuard:
    def test_clean_match_final(self) -> None:
        result = apply_guard(DecisionState.CLEAN_MATCH)
        assert result == DecisionState.CLEAN_MATCH

    def test_deterministic_exception_final(self) -> None:
        result = apply_guard(DecisionState.DETERMINISTIC_EXCEPTION)
        assert result == DecisionState.DETERMINISTIC_EXCEPTION

    def test_math_discrepancy_no_ai_response_unresolved(self) -> None:
        result = apply_guard(DecisionState.MATH_DISCREPANCY, ai_response=None)
        assert result == DecisionState.UNRESOLVED

    def test_math_discrepancy_valid_ai_keeps_decision(self) -> None:
        ai = _make_ai_response()
        packet = _make_evidence_packet()
        result = apply_guard(DecisionState.MATH_DISCREPANCY, ai, packet)
        assert result == DecisionState.MATH_DISCREPANCY

    def test_math_discrepancy_invalid_citations_unresolved(self) -> None:
        ai = _make_ai_response(cited_evidence=["nonexistent_evidence"])
        packet = _make_evidence_packet()
        result = apply_guard(DecisionState.MATH_DISCREPANCY, ai, packet)
        assert result == DecisionState.UNRESOLVED

    def test_math_discrepancy_empty_explanation_unresolved(self) -> None:
        ai = _make_ai_response(explanation="  ")
        packet = _make_evidence_packet()
        result = apply_guard(DecisionState.MATH_DISCREPANCY, ai, packet)
        assert result == DecisionState.UNRESOLVED

    def test_math_discrepancy_no_cited_evidence_unresolved(self) -> None:
        ai = _make_ai_response(cited_evidence=[])
        packet = _make_evidence_packet()
        result = apply_guard(DecisionState.MATH_DISCREPANCY, ai, packet)
        assert result == DecisionState.UNRESOLVED

    def test_unprocessed_always_final(self) -> None:
        result = apply_guard(DecisionState.UNPROCESSED)
        assert result == DecisionState.UNPROCESSED
