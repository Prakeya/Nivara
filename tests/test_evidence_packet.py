"""
Tests for EvidencePacketV2 typed evidence sub-models.

Covers:
- Citation ID generation
- Citation validation
- Prompt serialization (deterministic)
- Field retrieval by ID
- Edge cases: empty packet, partial evidence
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from backend.evidence_packet import (
    EvidencePacketV2,
    FeeEvidence,
    TaxEvidence,
    TimingEvidence,
    RefundEvidence,
    BankCreditEvidence,
    DuplicateEvidence,
    LinkageEvidence,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_fee_evidence() -> FeeEvidence:
    return FeeEvidence(
        computed_fee_paise=200,
        reported_fee_paise=250,
        formula_used="floor(amount * 0.02)",
        discrepancy_paise=50,
    )


def _make_tax_evidence() -> TaxEvidence:
    return TaxEvidence(
        computed_tax_paise=36,
        reported_tax_paise=45,
        rate_applied="0.18",
        discrepancy_paise=9,
    )


def _make_timing_evidence() -> TimingEvidence:
    return TimingEvidence(
        transaction_created_at=datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc),
        settlement_created_at=datetime(2026, 8, 29, 10, 0, 0, tzinfo=timezone.utc),
        bank_credited_at=datetime(2026, 8, 31, 10, 0, 0, tzinfo=timezone.utc),
        settlement_cycle_days=2,
        delay_days=0,
    )


def _make_refund_evidence() -> RefundEvidence:
    return RefundEvidence(
        refund_exists=True,
        refund_amount_paise=500,
        refund_created_at=datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc),
        settlement_has_refund=True,
    )


def _make_bank_credit_evidence() -> BankCreditEvidence:
    return BankCreditEvidence(
        bank_credit_exists=True,
        bank_credit_amount_paise=10000,
        bank_credit_utr="UTR_001",
        settlement_utr="UTR_001",
        utr_mismatch=False,
    )


def _make_duplicate_evidence() -> DuplicateEvidence:
    return DuplicateEvidence(
        is_duplicate=True,
        duplicate_of="SETL_002",
        duplicate_reason="Same UTR used in two settlements",
    )


def _make_linkage_evidence() -> LinkageEvidence:
    return LinkageEvidence(
        transaction_ids=["PAY_001", "PAY_002"],
        refund_ids=["REF_001"],
        bank_credit_ids=["BC_001"],
        linkage_confidence=1.0,
    )


def _make_full_packet() -> EvidencePacketV2:
    return EvidencePacketV2(
        settlement_id="SETL_001",
        fee_evidence=_make_fee_evidence(),
        tax_evidence=_make_tax_evidence(),
        timing_evidence=_make_timing_evidence(),
        refund_evidence=_make_refund_evidence(),
        bank_credit_evidence=_make_bank_credit_evidence(),
        duplicate_evidence=_make_duplicate_evidence(),
        linkage_evidence=_make_linkage_evidence(),
    )


def _make_empty_packet() -> EvidencePacketV2:
    return EvidencePacketV2(settlement_id="SETL_EMPTY")


def _make_partial_packet() -> EvidencePacketV2:
    return EvidencePacketV2(
        settlement_id="SETL_PARTIAL",
        fee_evidence=_make_fee_evidence(),
        bank_credit_evidence=_make_bank_credit_evidence(),
    )


# ---------------------------------------------------------------------------
# Tests: get_valid_citation_ids
# ---------------------------------------------------------------------------


class TestCitationIds:
    def test_get_valid_citation_ids_with_all_evidence(self) -> None:
        packet = _make_full_packet()
        ids = packet.get_valid_citation_ids()
        assert ids == {
            "fee_evidence",
            "tax_evidence",
            "timing_evidence",
            "refund_evidence",
            "bank_credit_evidence",
            "duplicate_evidence",
            "linkage_evidence",
        }

    def test_get_valid_citation_ids_with_no_evidence(self) -> None:
        packet = _make_empty_packet()
        ids = packet.get_valid_citation_ids()
        assert ids == set()

    def test_get_valid_citation_ids_with_partial_evidence(self) -> None:
        packet = _make_partial_packet()
        ids = packet.get_valid_citation_ids()
        assert ids == {"fee_evidence", "bank_credit_evidence"}

    def test_validate_citation_valid(self) -> None:
        packet = _make_full_packet()
        assert packet.validate_citation("fee_evidence") is True

    def test_validate_citation_invalid(self) -> None:
        packet = _make_full_packet()
        assert packet.validate_citation("nonexistent_evidence") is False

    def test_validate_citation_empty_packet(self) -> None:
        packet = _make_empty_packet()
        assert packet.validate_citation("fee_evidence") is False


# ---------------------------------------------------------------------------
# Tests: serialize_for_prompt
# ---------------------------------------------------------------------------


class TestSerializeForPrompt:
    def test_serialize_for_prompt_is_deterministic(self) -> None:
        packet = _make_full_packet()
        result1 = packet.serialize_for_prompt()
        result2 = packet.serialize_for_prompt()
        assert result1 == result2

    def test_serialize_for_prompt_contains_settlement_id(self) -> None:
        packet = _make_full_packet()
        output = packet.serialize_for_prompt()
        assert "SETL_001" in output

    def test_serialize_for_prompt_contains_all_fields(self) -> None:
        packet = _make_full_packet()
        output = packet.serialize_for_prompt()
        assert "Fee Evidence" in output
        assert "Tax Evidence" in output
        assert "Timing Evidence" in output
        assert "Refund Evidence" in output
        assert "Bank Credit Evidence" in output
        assert "Duplicate Evidence" in output
        assert "Linkage Evidence" in output

    def test_serialize_for_prompt_empty_packet(self) -> None:
        packet = _make_empty_packet()
        output = packet.serialize_for_prompt()
        assert "SETL_EMPTY" in output
        assert "Fee Evidence" not in output

    def test_serialize_for_prompt_partial_packet(self) -> None:
        packet = _make_partial_packet()
        output = packet.serialize_for_prompt()
        assert "Fee Evidence" in output
        assert "Tax Evidence" not in output

    def test_serialize_for_prompt_contains_amounts(self) -> None:
        packet = _make_full_packet()
        output = packet.serialize_for_prompt()
        assert "200 paise" in output  # computed fee
        assert "250 paise" in output  # reported fee
        assert "50 paise" in output   # discrepancy


# ---------------------------------------------------------------------------
# Tests: get_field_by_id
# ---------------------------------------------------------------------------


class TestGetFieldById:
    def test_get_field_by_id_valid(self) -> None:
        packet = _make_full_packet()
        field = packet.get_field_by_id("fee_evidence")
        assert field is not None
        assert isinstance(field, FeeEvidence)

    def test_get_field_by_id_invalid(self) -> None:
        packet = _make_full_packet()
        field = packet.get_field_by_id("nonexistent")
        assert field is None

    def test_get_field_by_id_empty_packet(self) -> None:
        packet = _make_empty_packet()
        field = packet.get_field_by_id("fee_evidence")
        assert field is None

    def test_get_field_by_id_all_fields(self) -> None:
        packet = _make_full_packet()
        for eid in packet.get_valid_citation_ids():
            field = packet.get_field_by_id(eid)
            assert field is not None, f"Field {eid} should exist"


# ---------------------------------------------------------------------------
# Tests: Evidence sub-models
# ---------------------------------------------------------------------------


class TestEvidenceSubModels:
    def test_fee_evidence_frozen(self) -> None:
        fee = _make_fee_evidence()
        with pytest.raises(Exception):
            fee.computed_fee_paise = 999  # type: ignore[misc]

    def test_tax_evidence_frozen(self) -> None:
        tax = _make_tax_evidence()
        with pytest.raises(Exception):
            tax.computed_tax_paise = 999  # type: ignore[misc]

    def test_timing_evidence_frozen(self) -> None:
        timing = _make_timing_evidence()
        with pytest.raises(Exception):
            timing.delay_days = 999  # type: ignore[misc]

    def test_duplicate_evidence_frozen(self) -> None:
        dup = _make_duplicate_evidence()
        with pytest.raises(Exception):
            dup.is_duplicate = False  # type: ignore[misc]

    def test_linkage_evidence_confidence_bounds(self) -> None:
        with pytest.raises(Exception):
            LinkageEvidence(linkage_confidence=1.5)

    def test_linkage_evidence_negative_confidence(self) -> None:
        with pytest.raises(Exception):
            LinkageEvidence(linkage_confidence=-0.1)

    def test_bank_credit_evidence_utr_mismatch(self) -> None:
        bc = BankCreditEvidence(
            bank_credit_exists=True,
            bank_credit_utr="UTR_001",
            settlement_utr="UTR_002",
            utr_mismatch=True,
        )
        assert bc.utr_mismatch is True
