from datetime import datetime, date
from enum import Enum
from typing import Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TransactionStatus(str, Enum):
    CAPTURED = "captured"
    FAILED = "failed"


class PaymentMethod(str, Enum):
    UPI = "upi"
    CARD = "card"
    NETBANKING = "netbanking"


class SettlementStatus(str, Enum):
    SETTLED = "settled"
    PENDING = "pending"


class RefundStatus(str, Enum):
    PROCESSED = "processed"


class DecisionState(str, Enum):
    CLEAN_MATCH = "CLEAN_MATCH"
    DETERMINISTIC_EXCEPTION = "DETERMINISTIC_EXCEPTION"
    MATH_DISCREPANCY = "MATH_DISCREPANCY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNRESOLVED = "UNRESOLVED"
    UNPROCESSED = "UNPROCESSED"
    AUTO_RESOLVED = "AUTO_RESOLVED"
    RESOLVED_BY_HUMAN = "RESOLVED_BY_HUMAN"
    REJECTED = "REJECTED"


class AIClassification(str, Enum):
    TIMING_MISMATCH = "TIMING_MISMATCH"
    REFUND_TIMING = "REFUND_TIMING"
    UNEXPLAINED = "UNEXPLAINED"


class AIRecommendedAction(str, Enum):
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"
    AUTO_RESOLVE = "AUTO_RESOLVE"


class ConfidenceTier(str, Enum):
    TIER_1 = "TIER_1"  # 0.95-1.0: eligible for auto-resolve
    TIER_2 = "TIER_2"  # 0.80-0.94: escalate with full evidence
    TIER_3 = "TIER_3"  # <0.80: escalate with INSUFFICIENT_EVIDENCE


class AgentActionType(str, Enum):
    THOUGHT = "THOUGHT"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    DECISION = "DECISION"


class ResolutionStatus(str, Enum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    CLOSED = "CLOSED"
    AUTO_CLOSED = "AUTO_CLOSED"
    HUMAN_CLOSED = "HUMAN_CLOSED"
    REJECTED = "REJECTED"


class ValidationResult(str, Enum):
    PASSED = "passed"
    FAILED = "failed"


class LinkedPaymentsSummary(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
    )

    count: int = Field(ge=0)
    total_paise: int = Field(ge=0)
    methods: list[PaymentMethod]


class LinkedRefundsSummary(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
    )

    count: int = Field(ge=0)
    total_paise: int = Field(ge=0)


class FeesSummary(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
    )

    total_paise: int = Field(ge=0)
    structure_applied: str
    validation_result: ValidationResult


class TaxSummary(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
    )

    total_paise: int = Field(ge=0)
    derivation_rule: str
    validation_result: ValidationResult


class BankCreditEvidence(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
    )

    utr: str
    amount_paise: int = Field(gt=0)
    date: date


class TimingEvidence(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
    )

    settlement_created_at: datetime
    settled_at: datetime
    bank_credited_at: datetime
    expected_cycle_days: int = Field(ge=0)


class Transaction(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
    )

    payment_id: str
    order_id: str
    amount: int = Field(gt=0)
    status: TransactionStatus
    method: PaymentMethod
    fee: int = Field(ge=0)
    tax: int = Field(ge=0)
    customer_email: Optional[str] = None
    created_at: datetime
    settlement_id: Optional[str] = None


class Settlement(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
    )

    settlement_id: str
    amount: int = Field(gt=0)
    status: SettlementStatus
    utr: str
    created_at: datetime
    settled_at: datetime
    linked_payment_ids: list[str]
    linked_refund_ids: list[str]

    @field_validator("settled_at")
    @classmethod
    def validate_settled_at(cls, v: datetime, info) -> datetime:
        if "created_at" in info.data and v < info.data["created_at"]:
            raise ValueError("settled_at must be >= created_at")
        return v


class Refund(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
    )

    refund_id: str
    payment_id: str
    amount: int = Field(gt=0)
    status: RefundStatus
    created_at: datetime


class BankCredit(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
    )

    utr: Optional[str] = None
    amount: int = Field(gt=0)
    date: date
    description: Optional[str] = None
    bank_account: Optional[str] = None


class PaymentDetail(BaseModel):
    """Individual payment-level record for AI investigation context."""
    model_config = ConfigDict(strict=True, frozen=False, validate_assignment=True)

    payment_id: str
    amount_paise: int = Field(ge=0)
    method: PaymentMethod
    fee_paise: int = Field(ge=0)
    tax_paise: int = Field(ge=0)
    fee_expected_paise: int = Field(ge=0)
    tax_expected_paise: int = Field(ge=0)
    fee_mismatch: bool = False
    tax_mismatch: bool = False


class CrossSettlementContext(BaseModel):
    """Cross-settlement patterns the deterministic engine cannot see."""
    model_config = ConfigDict(strict=True, frozen=False, validate_assignment=True)

    batch_size: int = Field(ge=0)
    batch_fee_exception_rate: float = Field(ge=0.0, le=1.0)
    batch_refund_rate: float = Field(ge=0.0, le=1.0)
    batch_math_discrepancy_rate: float = Field(ge=0.0, le=1.0)
    merchant_fee_exceptions_in_batch: int = Field(ge=0)
    method_mix: dict = Field(default_factory=dict)


class EvidencePacket(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
    )

    evidence_packet_id: UUID = Field(default_factory=uuid4)
    settlement_id: str
    expected_amount_paise: int
    actual_amount_paise: int = Field(gt=0)
    difference_paise: int
    linked_payments_summary: LinkedPaymentsSummary
    linked_refunds_summary: LinkedRefundsSummary
    fees_summary: FeesSummary
    tax_summary: TaxSummary
    bank_credit: BankCreditEvidence
    timing: TimingEvidence
    deterministic_checks_passed: list[str]
    deterministic_checks_failed: list[str]
    payment_details: list[PaymentDetail] = Field(default_factory=list)
    cross_settlement: Optional[CrossSettlementContext] = None

    @model_validator(mode="after")
    def validate_no_pii(self) -> "EvidencePacket":
        data = self.model_dump()
        forbidden_fields = {"customer_email", "customer_name", "customer_phone"}
        for field in forbidden_fields:
            if field in data:
                raise ValueError(f"EvidencePacket must not contain PII field: {field}")
        return self


class AIResponse(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
        extra="forbid",
    )

    classification: AIClassification
    explanation: str
    raw_confidence: float = Field(ge=0.0, le=1.0)
    cited_evidence: list[str]
    recommended_action: Literal[AIRecommendedAction.ESCALATE_TO_HUMAN] = AIRecommendedAction.ESCALATE_TO_HUMAN

    @field_validator("raw_confidence")
    @classmethod
    def validate_confidence_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("raw_confidence must be between 0.0 and 1.0")
        return v


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
    )

    settlement_id: str
    decision: DecisionState
    difference_paise: int
    expected_amount_paise: int
    actual_amount_paise: int = Field(gt=0)
    ai_response: Optional[AIResponse] = None
    agent_response: Optional[AgentResponse] = None
    deterministic_checks_passed: list[str]
    deterministic_checks_failed: list[str]
    escalate_to_human: bool
    ai_mode: Optional[str] = None
    resolution_status: ResolutionStatus = ResolutionStatus.OPEN
    resolution_confidence: Optional[float] = None
    resolution_source: Optional[str] = None  # "deterministic" | "agent" | "human"
    auto_approved_by_ai: int = Field(default=0, ge=0, le=0)
    agent_iterations: int = Field(default=0, ge=0)
    agent_tool_calls: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_difference_consistency(self) -> "ReconciliationResult":
        expected = self.expected_amount_paise
        actual = self.actual_amount_paise
        difference = self.difference_paise
        if difference != actual - expected:
            raise ValueError(
                f"difference_paise ({difference}) must equal actual_amount_paise ({actual}) - expected_amount_paise ({expected})"
            )
        return self

    @model_validator(mode="after")
    def validate_clean_match(self) -> "ReconciliationResult":
        if self.decision == DecisionState.CLEAN_MATCH and self.difference_paise != 0:
            raise ValueError("CLEAN_MATCH requires difference_paise == 0")
        return self


class BatchMetrics(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
    )

    total_settlements: int = Field(ge=0)
    clean_matches: int = Field(ge=0)
    exceptions: int = Field(ge=0)
    unresolved: int = Field(ge=0)
    auto_approved_by_ai: int = Field(ge=0, le=0)
    ai_investigations: int = Field(ge=0)
    ai_invocation_rate: float = Field(ge=0.0, le=1.0)
    auto_resolved: int = Field(ge=0, default=0)
    human_reviewed: int = Field(ge=0, default=0)
    pending_review: int = Field(ge=0, default=0)
    loop_closure_rate: float = Field(ge=0.0, le=1.0, default=0.0)


class EvaluationResult(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
    )

    match_rate: float = Field(ge=0.0, le=1.0)
    false_accept_rate: float = Field(ge=0.0, le=1.0)
    safe_escalation_rate: float = Field(ge=0.0, le=1.0)
    ai_invocation_rate: float = Field(ge=0.0, le=1.0)
    ai_auto_approval_rate_pct: float = Field(ge=0.0, le=0.0)
    processing_time_per_settlement: float = Field(ge=0.0)


# ---------------------------------------------------------------------------
# Agentic models
# ---------------------------------------------------------------------------

class ReasoningStep(BaseModel):
    """A single step in the agent's reasoning chain."""
    model_config = ConfigDict(strict=True, frozen=False, validate_assignment=True)

    step_number: int = Field(ge=1)
    action_type: AgentActionType
    thought: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_result: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now())


class AgentTrace(BaseModel):
    """Full reasoning trace for an agent investigation."""
    model_config = ConfigDict(strict=True, frozen=False, validate_assignment=True)

    settlement_id: str
    steps: list[ReasoningStep] = Field(default_factory=list)
    final_classification: Optional[AIClassification] = None
    final_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    confidence_tier: ConfidenceTier = ConfidenceTier.TIER_3
    iteration_count: int = Field(ge=0, default=0)
    self_corrections: int = Field(ge=0, default=0)


class ToolCall(BaseModel):
    """A tool call requested by the agent."""
    model_config = ConfigDict(strict=True, frozen=False, validate_assignment=True)

    tool_name: str
    tool_args: dict = Field(default_factory=dict)
    call_id: str = Field(default_factory=lambda: str(uuid4()))


class ToolResult(BaseModel):
    """Result returned by an agent tool."""
    model_config = ConfigDict(strict=True, frozen=False, validate_assignment=True)

    call_id: str
    tool_name: str
    result: dict = Field(default_factory=dict)
    success: bool = True
    error: Optional[str] = None


class AgentResponse(BaseModel):
    """Full agent response including trace and decision."""
    model_config = ConfigDict(
        strict=True,
        frozen=False,
        validate_assignment=True,
        extra="forbid",
    )

    classification: AIClassification
    explanation: str
    raw_confidence: float = Field(ge=0.0, le=1.0)
    cited_evidence: list[str]
    recommended_action: AIRecommendedAction
    trace: AgentTrace
    tool_calls_made: int = Field(ge=0, default=0)
    reasoning_summary: str = ""

    @field_validator("raw_confidence")
    @classmethod
    def validate_confidence_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("raw_confidence must be between 0.0 and 1.0")
        return v


class HumanReviewDecision(BaseModel):
    """A human reviewer's decision on a settlement."""
    model_config = ConfigDict(strict=True, frozen=False, validate_assignment=True)

    settlement_id: str
    decision: str  # "APPROVE" | "REJECT" | "MODIFY"
    reason: str
    reviewer_id: str
    modifications: Optional[dict] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now())


class ReviewDecision(BaseModel):
    """Request body for human review submission."""
    model_config = ConfigDict(strict=True, frozen=False, validate_assignment=True)

    decision: str  # "APPROVE" | "REJECT" | "MODIFY"
    reason: str = Field(max_length=500)
    reviewer_id: str = Field(default="anonymous", max_length=100)


class LoopClosureMetrics(BaseModel):
    """Metrics for loop closure tracking."""
    model_config = ConfigDict(strict=True, frozen=False, validate_assignment=True)

    total_settlements: int = Field(ge=0)
    closed_by_ai: int = Field(ge=0)
    closed_by_human: int = Field(ge=0)
    pending_review: int = Field(ge=0)
    rejected: int = Field(ge=0)
    loop_closure_rate: float = Field(ge=0.0, le=1.0)
    auto_resolution_rate: float = Field(ge=0.0, le=1.0)
    avg_time_to_close_seconds: float = Field(ge=0.0)


class AgentToolDefinition(BaseModel):
    """Definition of a tool available to the agent."""
    model_config = ConfigDict(strict=True, frozen=False, validate_assignment=True)

    name: str
    description: str
    parameters: dict = Field(default_factory=dict)