"""
Tests for model_selector — evidence complexity classification and model selection.
"""

from __future__ import annotations

from backend.model_selector import (
    evidence_complexity,
    select_model,
    COMPLEXITY_SIMPLE,
    COMPLEXITY_COMPLEX,
    MODEL_FOR_COMPLEXITY,
)
from backend.evidence_packet import EvidencePacketV2, FeeEvidence, TaxEvidence, TimingEvidence
from backend.groq_client import DEFAULT_MODEL, FALLBACK_MODEL


class TestEvidenceComplexity:
    def test_none_packet_is_simple(self) -> None:
        assert evidence_complexity(None) == COMPLEXITY_SIMPLE

    def test_no_evidence_is_simple(self) -> None:
        packet = EvidencePacketV2.model_construct(settlement_id="set_1")
        assert evidence_complexity(packet) == COMPLEXITY_SIMPLE

    def test_1_type_is_simple(self) -> None:
        packet = EvidencePacketV2.model_construct(
            settlement_id="set_1",
            fee_evidence=FeeEvidence(
                computed_fee_paise=100, reported_fee_paise=120,
                formula_used="1%", discrepancy_paise=20,
            ),
        )
        assert evidence_complexity(packet) == COMPLEXITY_SIMPLE
        assert evidence_complexity(packet.model_copy(deep=True)) == COMPLEXITY_SIMPLE

    def test_2_types_is_simple(self) -> None:
        packet = EvidencePacketV2.model_construct(
            settlement_id="set_1",
            fee_evidence=_fee(),
            timing_evidence=TimingEvidence(),
        )
        assert evidence_complexity(packet) == COMPLEXITY_SIMPLE

    def test_3_types_is_complex(self) -> None:
        packet = EvidencePacketV2.model_construct(
            settlement_id="set_1",
            fee_evidence=_fee(),
            timing_evidence=TimingEvidence(),
            refund_evidence=_refund(),
        )
        assert evidence_complexity(packet) == COMPLEXITY_COMPLEX


class TestSelectModel:
    def test_select_none_uses_simple_model(self) -> None:
        assert select_model(None) == MODEL_FOR_COMPLEXITY[COMPLEXITY_SIMPLE]
        assert select_model(None) == FALLBACK_MODEL

    def test_simple_uses_fallback_model(self) -> None:
        assert MODEL_FOR_COMPLEXITY[COMPLEXITY_SIMPLE] == FALLBACK_MODEL
        assert FALLBACK_MODEL == "groq/compound-mini"

    def test_complex_uses_default_model(self) -> None:
        assert MODEL_FOR_COMPLEXITY[COMPLEXITY_COMPLEX] == DEFAULT_MODEL
        assert DEFAULT_MODEL == "openai/gpt-oss-20b"

    def test_select_2_type_packet(self) -> None:
        packet = EvidencePacketV2.model_construct(
            settlement_id="set_1",
            fee_evidence=_fee(),
            timing_evidence=TimingEvidence(),
        )
        assert select_model(packet) == FALLBACK_MODEL

    def test_select_3_type_packet(self) -> None:
        packet = EvidencePacketV2.model_construct(
            settlement_id="set_1",
            fee_evidence=_fee(),
            timing_evidence=TimingEvidence(),
            refund_evidence=_refund(),
        )
        assert select_model(packet) == DEFAULT_MODEL


def _fee() -> FeeEvidence:
    return FeeEvidence(
        computed_fee_paise=100, reported_fee_paise=120,
        formula_used="1%", discrepancy_paise=20,
    )


def _refund():
    from backend.evidence_packet import RefundEvidence
    return RefundEvidence(refund_exists=False)