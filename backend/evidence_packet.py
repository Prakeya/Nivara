"""
Evidence Packet V2: Typed evidence sub-models for deterministic + AI architecture.

The EvidencePacketV2 is the contract between the deterministic engine and the AI
investigator. The engine populates it; the AI reads it. The AI can only cite
evidence IDs that exist in this packet.

Design Principles:
- All amounts in paise (integer). No floats.
- All datetimes timezone-aware (UTC).
- serialize_for_prompt() is deterministic (sorted keys).
- No PII in evidence fields.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class FeeEvidence(BaseModel):
    """Per-payment fee computation evidence."""

    model_config = ConfigDict(strict=True, frozen=True)

    computed_fee_paise: int
    reported_fee_paise: int
    formula_used: str
    discrepancy_paise: int


class TaxEvidence(BaseModel):
    """Per-payment tax computation evidence."""

    model_config = ConfigDict(strict=True, frozen=True)

    computed_tax_paise: int
    reported_tax_paise: int
    rate_applied: str
    discrepancy_paise: int


class TimingEvidence(BaseModel):
    """Timing evidence for settlement cycle analysis."""

    model_config = ConfigDict(strict=True, frozen=True)

    transaction_created_at: Optional[datetime] = None
    settlement_created_at: Optional[datetime] = None
    bank_credited_at: Optional[datetime] = None
    settlement_cycle_days: int = 2
    delay_days: int = 0


class RefundEvidence(BaseModel):
    """Refund linkage evidence."""

    model_config = ConfigDict(strict=True, frozen=True)

    refund_exists: bool
    refund_amount_paise: Optional[int] = None
    refund_created_at: Optional[datetime] = None
    settlement_has_refund: bool = False


class BankCreditEvidence(BaseModel):
    """Bank credit matching evidence."""

    model_config = ConfigDict(strict=True, frozen=True)

    bank_credit_exists: bool
    bank_credit_amount_paise: Optional[int] = None
    bank_credit_utr: Optional[str] = None
    settlement_utr: Optional[str] = None
    utr_mismatch: bool = False


class DuplicateEvidence(BaseModel):
    """Duplicate detection evidence."""

    model_config = ConfigDict(strict=True, frozen=True)

    is_duplicate: bool
    duplicate_of: Optional[str] = None
    duplicate_reason: Optional[str] = None


class LinkageEvidence(BaseModel):
    """Entity linkage evidence."""

    model_config = ConfigDict(strict=True, frozen=True)

    transaction_ids: list[str] = Field(default_factory=list)
    refund_ids: list[str] = Field(default_factory=list)
    bank_credit_ids: list[str] = Field(default_factory=list)
    linkage_confidence: float = Field(ge=0.0, le=1.0, default=1.0)


class EvidencePacketV2(BaseModel):
    """
    Structured evidence for AI investigation.

    The engine populates this packet during reconcile_settlement().
    The AI investigator receives it and can only cite evidence IDs
    that exist in this packet.

    Evidence IDs follow the pattern: <field_name> or <field_name>:<sub_id>
    """

    model_config = ConfigDict(strict=True, frozen=False, validate_assignment=True)

    settlement_id: str
    fee_evidence: Optional[FeeEvidence] = None
    tax_evidence: Optional[TaxEvidence] = None
    timing_evidence: Optional[TimingEvidence] = None
    refund_evidence: Optional[RefundEvidence] = None
    bank_credit_evidence: Optional[BankCreditEvidence] = None
    duplicate_evidence: Optional[DuplicateEvidence] = None
    linkage_evidence: Optional[LinkageEvidence] = None

    def get_valid_citation_ids(self) -> set[str]:
        """
        Return set of valid evidence IDs the AI can cite.

        Returns:
            Set of strings like "fee_evidence", "tax_evidence:payment_123", etc.
        """
        ids: set[str] = set()
        if self.fee_evidence is not None:
            ids.add("fee_evidence")
        if self.tax_evidence is not None:
            ids.add("tax_evidence")
        if self.timing_evidence is not None:
            ids.add("timing_evidence")
        if self.refund_evidence is not None:
            ids.add("refund_evidence")
        if self.bank_credit_evidence is not None:
            ids.add("bank_credit_evidence")
        if self.duplicate_evidence is not None:
            ids.add("duplicate_evidence")
        if self.linkage_evidence is not None:
            ids.add("linkage_evidence")
        return ids

    def validate_citation(self, citation_id: str) -> bool:
        """
        Check if a citation ID is valid.

        Args:
            citation_id: The evidence ID to validate.

        Returns:
            True if the citation ID exists in this packet.
        """
        return citation_id in self.get_valid_citation_ids()

    def serialize_for_prompt(self) -> str:
        """
        Return structured markdown for LLM prompt.

        Deterministic: keys are sorted, output is reproducible.
        No PII is included.
        """
        sections: list[str] = []

        sections.append(f"## Settlement: {self.settlement_id}\n")

        if self.fee_evidence is not None:
            fee = self.fee_evidence
            sections.append("### Fee Evidence")
            sections.append(f"- Computed fee: {fee.computed_fee_paise} paise")
            sections.append(f"- Reported fee: {fee.reported_fee_paise} paise")
            sections.append(f"- Formula: {fee.formula_used}")
            sections.append(f"- Discrepancy: {fee.discrepancy_paise} paise\n")

        if self.tax_evidence is not None:
            tax = self.tax_evidence
            sections.append("### Tax Evidence")
            sections.append(f"- Computed tax: {tax.computed_tax_paise} paise")
            sections.append(f"- Reported tax: {tax.reported_tax_paise} paise")
            sections.append(f"- Rate: {tax.rate_applied}")
            sections.append(f"- Discrepancy: {tax.discrepancy_paise} paise\n")

        if self.timing_evidence is not None:
            timing = self.timing_evidence
            sections.append("### Timing Evidence")
            sections.append(f"- Transaction created: {timing.transaction_created_at}")
            sections.append(f"- Settlement created: {timing.settlement_created_at}")
            sections.append(f"- Bank credited: {timing.bank_credited_at}")
            sections.append(f"- Cycle days: {timing.settlement_cycle_days}")
            sections.append(f"- Delay days: {timing.delay_days}\n")

        if self.refund_evidence is not None:
            refund = self.refund_evidence
            sections.append("### Refund Evidence")
            sections.append(f"- Refund exists: {refund.refund_exists}")
            sections.append(f"- Refund amount: {refund.refund_amount_paise} paise")
            sections.append(f"- Settlement has refund: {refund.settlement_has_refund}\n")

        if self.bank_credit_evidence is not None:
            bc = self.bank_credit_evidence
            sections.append("### Bank Credit Evidence")
            sections.append(f"- Bank credit exists: {bc.bank_credit_exists}")
            sections.append(f"- Bank credit amount: {bc.bank_credit_amount_paise} paise")
            sections.append(f"- Bank credit UTR: {bc.bank_credit_utr}")
            sections.append(f"- Settlement UTR: {bc.settlement_utr}")
            sections.append(f"- UTR mismatch: {bc.utr_mismatch}\n")

        if self.duplicate_evidence is not None:
            dup = self.duplicate_evidence
            sections.append("### Duplicate Evidence")
            sections.append(f"- Is duplicate: {dup.is_duplicate}")
            sections.append(f"- Duplicate of: {dup.duplicate_of}")
            sections.append(f"- Reason: {dup.duplicate_reason}\n")

        if self.linkage_evidence is not None:
            link = self.linkage_evidence
            sections.append("### Linkage Evidence")
            sections.append(f"- Transaction IDs: {link.transaction_ids}")
            sections.append(f"- Refund IDs: {link.refund_ids}")
            sections.append(f"- Bank credit IDs: {link.bank_credit_ids}")
            sections.append(f"- Confidence: {link.linkage_confidence}\n")

        return "\n".join(sections)

    def get_field_by_id(self, evidence_id: str) -> Optional[BaseModel]:
        """
        Return evidence sub-model by string ID.

        Args:
            evidence_id: The evidence field name (e.g., "fee_evidence").

        Returns:
            The corresponding Pydantic model, or None if not found.
        """
        field_map: dict[str, Any] = {
            "fee_evidence": self.fee_evidence,
            "tax_evidence": self.tax_evidence,
            "timing_evidence": self.timing_evidence,
            "refund_evidence": self.refund_evidence,
            "bank_credit_evidence": self.bank_credit_evidence,
            "duplicate_evidence": self.duplicate_evidence,
            "linkage_evidence": self.linkage_evidence,
        }
        return field_map.get(evidence_id)
