"""
Tests for AI Validator — parse, validate citations, track cost.

Covers:
- parse_ai_response: valid/invalid JSON
- validate_citations: valid/invalid evidence IDs
- compute_cost: provider pricing
- validate_ai_response: full pipeline
"""

from __future__ import annotations

import pytest

from backend.models import AIResponse, AIClassification
from backend.evidence_packet import EvidencePacketV2
from backend.ai_validator import (
    parse_ai_response,
    validate_citations,
    compute_cost,
    validate_ai_response,
    AIValidationResult,
)
from tests.test_evidence_packet import _make_full_packet, _make_empty_packet


# ---------------------------------------------------------------------------
# Tests: parse_ai_response
# ---------------------------------------------------------------------------


class TestParseAiResponse:
    def test_parse_valid_response(self) -> None:
        raw = {
            "classification": "TIMING_MISMATCH",
            "explanation": "Settlement delayed by 2 days",
            "confidence": 0.85,
            "cited_evidence": ["timing_evidence"],
        }
        result = parse_ai_response(raw)
        assert result is not None
        assert result.classification == AIClassification.TIMING_MISMATCH
        assert result.explanation == "Settlement delayed by 2 days"
        assert result.raw_confidence == 0.85
        assert result.cited_evidence == ["timing_evidence"]

    def test_parse_invalid_classification(self) -> None:
        raw = {
            "classification": "NONEXISTENT",
            "explanation": "test",
            "confidence": 0.8,
            "cited_evidence": [],
        }
        assert parse_ai_response(raw) is None

    def test_parse_empty_explanation(self) -> None:
        raw = {
            "classification": "TIMING_MISMATCH",
            "explanation": "  ",
            "confidence": 0.8,
            "cited_evidence": [],
        }
        assert parse_ai_response(raw) is None

    def test_parse_confidence_out_of_range(self) -> None:
        raw = {
            "classification": "TIMING_MISMATCH",
            "explanation": "test",
            "confidence": 1.5,
            "cited_evidence": [],
        }
        assert parse_ai_response(raw) is None

    def test_parse_missing_classification(self) -> None:
        raw = {"explanation": "test", "confidence": 0.8, "cited_evidence": []}
        assert parse_ai_response(raw) is None

    def test_parse_non_list_cited_evidence(self) -> None:
        raw = {
            "classification": "TIMING_MISMATCH",
            "explanation": "test",
            "confidence": 0.8,
            "cited_evidence": "fee_evidence",
        }
        assert parse_ai_response(raw) is None


# ---------------------------------------------------------------------------
# Tests: validate_citations
# ---------------------------------------------------------------------------


class TestValidateCitations:
    def test_all_citations_valid(self) -> None:
        packet = _make_full_packet()
        ai = AIResponse(
            classification=AIClassification.TIMING_MISMATCH,
            explanation="test",
            raw_confidence=0.8,
            cited_evidence=["fee_evidence", "tax_evidence"],
        )
        invalid = validate_citations(ai, packet)
        assert invalid == []

    def test_invalid_citation(self) -> None:
        packet = _make_full_packet()
        ai = AIResponse(
            classification=AIClassification.TIMING_MISMATCH,
            explanation="test",
            raw_confidence=0.8,
            cited_evidence=["fee_evidence", "nonexistent"],
        )
        invalid = validate_citations(ai, packet)
        assert invalid == ["nonexistent"]

    def test_empty_packet_all_invalid(self) -> None:
        packet = _make_empty_packet()
        ai = AIResponse(
            classification=AIClassification.TIMING_MISMATCH,
            explanation="test",
            raw_confidence=0.8,
            cited_evidence=["fee_evidence"],
        )
        invalid = validate_citations(ai, packet)
        assert invalid == ["fee_evidence"]

    def test_no_citations(self) -> None:
        packet = _make_full_packet()
        ai = AIResponse(
            classification=AIClassification.TIMING_MISMATCH,
            explanation="test",
            raw_confidence=0.8,
            cited_evidence=[],
        )
        invalid = validate_citations(ai, packet)
        assert invalid == []


# ---------------------------------------------------------------------------
# Tests: compute_cost
# ---------------------------------------------------------------------------


class TestComputeCost:
    def test_openai_cost(self) -> None:
        cost = compute_cost("openai", 1000, 500)
        assert cost == pytest.approx(0.003, abs=0.001)

    def test_anthropic_cost(self) -> None:
        cost = compute_cost("anthropic", 1000, 500)
        assert cost == pytest.approx(0.0045, abs=0.001)

    def test_local_cost_zero(self) -> None:
        cost = compute_cost("local", 1000, 500)
        assert cost == 0.0

    def test_unknown_provider_default(self) -> None:
        cost = compute_cost("unknown", 1000, 500)
        assert cost > 0.0


# ---------------------------------------------------------------------------
# Tests: validate_ai_response (full pipeline)
# ---------------------------------------------------------------------------


class TestValidateAiResponseFull:
    def test_valid_response(self) -> None:
        packet = _make_full_packet()
        raw = {
            "classification": "TIMING_MISMATCH",
            "explanation": "Settlement delayed",
            "confidence": 0.85,
            "cited_evidence": ["fee_evidence"],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        result = validate_ai_response(raw, packet, provider="openai", latency_ms=120)
        assert result.ai_response is not None
        assert result.metrics.provider == "openai"
        assert result.metrics.tokens_in == 100
        assert result.metrics.tokens_out == 50
        assert result.metrics.latency_ms == 120
        assert result.metrics.cost_inr > 0.0
        assert result.error is None

    def test_invalid_citations_returns_error(self) -> None:
        packet = _make_full_packet()
        raw = {
            "classification": "TIMING_MISMATCH",
            "explanation": "test",
            "confidence": 0.8,
            "cited_evidence": ["nonexistent"],
        }
        result = validate_ai_response(raw, packet)
        assert result.ai_response is None
        assert result.error is not None
        assert "nonexistent" in result.error

    def test_parse_failure_returns_error(self) -> None:
        packet = _make_full_packet()
        raw = {"bad": "response"}
        result = validate_ai_response(raw, packet)
        assert result.ai_response is None
        assert result.error is not None

    def test_metrics_populated_on_failure(self) -> None:
        packet = _make_full_packet()
        raw = {"bad": "response"}
        result = validate_ai_response(raw, packet, provider="anthropic", latency_ms=200)
        assert result.metrics.provider == "anthropic"
        assert result.metrics.latency_ms == 200
