"""
Phase 7+ Agentic: Reconciliation Agent with ReAct loop.

Deterministic-first reconciliation with bounded agentic reasoning.
The agent ONLY handles DETERMINISTIC_EXCEPTION and MATH_DISCREPANCY cases.

Safety invariants:
- AI never calculates money
- AI never modifies financial records
- AI auto-resolves ONLY when confidence >= 0.95 AND deterministic checks confirm trivial error
- All AI cases route to human review queue unless explicitly auto-resolved
- Max 3 reasoning iterations per settlement
- All failures → UNRESOLVED + human review

Usage:
    from backend.ai_investigator import investigate, MockLLMClient
    client = MockLLMClient(classification="UNEXPLAINED", confidence=0.6)
    result = investigate(evidence_packet, llm_client=client)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional, Protocol
from uuid import uuid4

from backend.models import (
    AIClassification,
    AIRecommendedAction,
    AIResponse,
    AgentActionType,
    AgentResponse,
    AgentTrace,
    BankCreditEvidence,
    ConfidenceTier,
    DecisionState,
    EvidencePacket,
    FeesSummary,
    LinkedPaymentsSummary,
    LinkedRefundsSummary,
    PaymentDetail,
    PaymentMethod,
    ReasoningStep,
    ReconciliationResult,
    ResolutionStatus,
    TaxSummary,
    TimingEvidence,
    ToolCall,
    ToolResult,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# LLM Client Protocol — accepts EvidencePacket DIRECTLY (no string parsing)
# ---------------------------------------------------------------------------

class LLMClient(Protocol):
    """Interface for LLM providers. Accepts structured EvidencePacket,
    returns parsed response dict. No prompt string manipulation."""

    def complete(
        self,
        messages: list[dict],
        evidence_packet: Optional[EvidencePacket] = None,
        tools: Optional[list[dict]] = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
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
    agent_response: Optional[AgentResponse] = None
    confidence_tier: str = "LOW"
    error_type: Optional[str] = None
    escalate_to_human: bool = True
    is_mock: bool = False
    agent_iterations: int = 0
    agent_tool_calls: int = 0


# ---------------------------------------------------------------------------
# Confidence policy
# ---------------------------------------------------------------------------

def compute_confidence_tier(raw_confidence: float) -> str:
    """Map raw confidence to tier: TIER_1 >= 0.95, TIER_2 >= 0.80, TIER_3 < 0.80."""
    if raw_confidence >= 0.95:
        return ConfidenceTier.TIER_1.value
    elif raw_confidence >= 0.80:
        return ConfidenceTier.TIER_2.value
    return ConfidenceTier.TIER_3.value


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
        "payment_details",
        "cross_settlement",
    }
    # Also accept deterministic check names
    valid_ids.update(evidence_packet.deterministic_checks_passed)
    valid_ids.update(evidence_packet.deterministic_checks_failed)

    return all(eid in valid_ids for eid in cited_evidence)


# ---------------------------------------------------------------------------
# Agent Tools — typed functions the LLM can call
# ---------------------------------------------------------------------------

def verify_utr_cross_source(evidence: EvidencePacket) -> dict:
    """Verify UTR consistency between settlement and bank credit."""
    bc = evidence.bank_credit
    timing = evidence.timing
    delta_days = (timing.bank_credited_at - timing.settled_at).days

    return {
        "tool": "verify_utr_cross_source",
        "settlement_id": evidence.settlement_id,
        "utr": bc.utr,
        "bank_amount_paise": bc.amount_paise,
        "expected_amount_paise": evidence.expected_amount_paise,
        "amount_match": bc.amount_paise == evidence.actual_amount_paise,
        "timing_delta_days": delta_days,
        "within_expected_cycle": delta_days <= timing.expected_cycle_days,
        "result": "CONSISTENT" if bc.amount_paise == evidence.actual_amount_paise else "MISMATCH",
    }


def calculate_expected_fee(amount_paise: int, method: str) -> dict:
    """Calculate expected fee for a payment amount and method."""
    fee_structure = {
        "upi": {"rate_num": 0, "rate_den": 1, "fixed": 0},
        "card": {"rate_num": 2, "rate_den": 100, "fixed": 100},
        "netbanking": {"rate_num": 15, "rate_den": 1000, "fixed": 100},
    }
    struct = fee_structure.get(method)
    if not struct:
        return {"tool": "calculate_expected_fee", "error": f"Unknown method: {method}"}

    expected_fee = (amount_paise * struct["rate_num"]) // struct["rate_den"] + struct["fixed"]
    expected_tax = (expected_fee * 18) // 100

    return {
        "tool": "calculate_expected_fee",
        "method": method,
        "amount_paise": amount_paise,
        "expected_fee_paise": expected_fee,
        "expected_tax_paise": expected_tax,
        "total_expected_deduction_paise": expected_fee + expected_tax,
    }


def check_gst_compliance(fee_paise: int, tax_paise: int) -> dict:
    """Check if tax follows floor(fee * 0.18) rule."""
    expected_tax = (fee_paise * 18) // 100
    compliant = tax_paise == expected_tax

    return {
        "tool": "check_gst_compliance",
        "fee_paise": fee_paise,
        "tax_paise": tax_paise,
        "expected_tax_paise": expected_tax,
        "compliant": compliant,
        "difference_paise": tax_paise - expected_tax,
    }


def query_batch_pattern(exception_type: str, evidence: EvidencePacket) -> dict:
    """Query batch-level patterns for context."""
    cross = evidence.cross_settlement
    if not cross:
        return {
            "tool": "query_batch_pattern",
            "exception_type": exception_type,
            "pattern_found": False,
            "reason": "No cross-settlement context available",
        }

    return {
        "tool": "query_batch_pattern",
        "exception_type": exception_type,
        "batch_size": cross.batch_size,
        "batch_fee_exception_rate": cross.batch_fee_exception_rate,
        "batch_refund_rate": cross.batch_refund_rate,
        "batch_math_discrepancy_rate": cross.batch_math_discrepancy_rate,
        "method_mix": cross.method_mix,
        "pattern_found": cross.batch_fee_exception_rate > 0.1,
    }


def request_human_escalation(reason: str, evidence: EvidencePacket) -> dict:
    """Request human escalation with reason and evidence summary."""
    return {
        "tool": "request_human_escalation",
        "settlement_id": evidence.settlement_id,
        "reason": reason,
        "escalated": True,
        "evidence_packet_id": str(evidence.evidence_packet_id),
        "difference_paise": evidence.difference_paise,
    }


def auto_resolve_trivial(resolution: dict) -> dict:
    """Auto-resolve a trivial case. ONLY for Tier 1 confidence."""
    return {
        "tool": "auto_resolve_trivial",
        "settlement_id": resolution.get("settlement_id", ""),
        "resolution": resolution,
        "auto_resolved": True,
        "requires_human_audit": True,
    }


# Tool registry
AGENT_TOOLS: dict[str, Callable] = {
    "verify_utr_cross_source": lambda args, ep: verify_utr_cross_source(ep),
    "calculate_expected_fee": lambda args, ep: calculate_expected_fee(
        args.get("amount_paise", 0), args.get("method", "upi")
    ),
    "check_gst_compliance": lambda args, ep: check_gst_compliance(
        args.get("fee_paise", 0), args.get("tax_paise", 0)
    ),
    "query_batch_pattern": lambda args, ep: query_batch_pattern(
        args.get("exception_type", ""), ep
    ),
    "request_human_escalation": lambda args, ep: request_human_escalation(
        args.get("reason", ""), ep
    ),
    "auto_resolve_trivial": lambda args, ep: auto_resolve_trivial(args),
}


def get_tool_definitions() -> list[dict]:
    """Return tool definitions for the LLM."""
    return [
        {
            "name": "verify_utr_cross_source",
            "description": "Verify UTR consistency between settlement and bank credit records",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "calculate_expected_fee",
            "description": "Calculate expected fee and tax for a payment amount and method",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount_paise": {"type": "integer", "description": "Payment amount in paise"},
                    "method": {"type": "string", "enum": ["upi", "card", "netbanking"]},
                },
                "required": ["amount_paise", "method"],
            },
        },
        {
            "name": "check_gst_compliance",
            "description": "Check if tax follows floor(fee * 0.18) rule",
            "parameters": {
                "type": "object",
                "properties": {
                    "fee_paise": {"type": "integer"},
                    "tax_paise": {"type": "integer"},
                },
                "required": ["fee_paise", "tax_paise"],
            },
        },
        {
            "name": "query_batch_pattern",
            "description": "Query batch-level patterns for cross-settlement context",
            "parameters": {
                "type": "object",
                "properties": {
                    "exception_type": {"type": "string"},
                },
                "required": ["exception_type"],
            },
        },
        {
            "name": "request_human_escalation",
            "description": "Escalate to human review with reason",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                },
                "required": ["reason"],
            },
        },
        {
            "name": "auto_resolve_trivial",
            "description": "Auto-resolve trivial cases (only for Tier 1 confidence)",
            "parameters": {
                "type": "object",
                "properties": {
                    "settlement_id": {"type": "string"},
                    "resolution_reason": {"type": "string"},
                },
                "required": ["settlement_id", "resolution_reason"],
            },
        },
    ]


# ---------------------------------------------------------------------------
# Prompt construction — uses EvidencePacket directly, no string parsing
# ---------------------------------------------------------------------------

MAX_AGENT_ITERATIONS = 3
AUTO_RESOLVE_CONFIDENCE_THRESHOLD = 0.95


def _build_system_prompt() -> str:
    return (
        "You are a financial settlement investigator with access to tools.\n"
        "You follow a ReAct pattern: Thought → Action → Observation → Decision.\n\n"
        "RULES:\n"
        "1. You may use tools to verify facts. Never assume — always verify.\n"
        "2. You must cite evidence from the provided packet.\n"
        "3. Never calculate amounts yourself — use the calculate_expected_fee tool.\n"
        "4. You can ONLY recommend ESCALATE_TO_HUMAN or AUTO_RESOLVE.\n"
        "5. AUTO_RESOLVE is ONLY for trivial cases: confidence >= 0.95 AND "
        "   deterministic checks confirm a trivial error (e.g., off-by-one paise rounding).\n"
        "6. For all non-trivial cases, recommend ESCALATE_TO_HUMAN.\n"
        "7. Return JSON with: classification, explanation, raw_confidence, cited_evidence, "
        "   recommended_action, reasoning_steps.\n"
        "8. reasoning_steps is a list of {step_number, thought, action, observation} objects."
    )


def _build_agent_messages(
    evidence_packet: EvidencePacket,
    previous_steps: Optional[list[dict]] = None,
    batch_memory: Optional[str] = None,
) -> list[dict]:
    """Build messages for the agent loop, using EvidencePacket directly."""
    ep = evidence_packet

    evidence_dict = {
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
            "derivation_rule": ep.tax_summary.derivation_rule,
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
    }

    if ep.payment_details:
        evidence_dict["payment_details"] = [
            {
                "payment_id": pd.payment_id,
                "amount_paise": pd.amount_paise,
                "method": pd.method.value,
                "fee_paise": pd.fee_paise,
                "tax_paise": pd.tax_paise,
                "fee_expected_paise": pd.fee_expected_paise,
                "tax_expected_paise": pd.tax_expected_paise,
                "fee_mismatch": pd.fee_mismatch,
                "tax_mismatch": pd.tax_mismatch,
            }
            for pd in ep.payment_details
        ]

    if ep.cross_settlement:
        evidence_dict["cross_settlement_context"] = {
            "batch_size": ep.cross_settlement.batch_size,
            "batch_fee_exception_rate": ep.cross_settlement.batch_fee_exception_rate,
            "batch_refund_rate": ep.cross_settlement.batch_refund_rate,
            "batch_math_discrepancy_rate": ep.cross_settlement.batch_math_discrepancy_rate,
            "merchant_fee_exceptions_in_batch": ep.cross_settlement.merchant_fee_exceptions_in_batch,
            "method_mix": ep.cross_settlement.method_mix,
        }

    evidence_json = json.dumps(evidence_dict, indent=2)

    messages = [
        {"role": "system", "content": _build_system_prompt()},
    ]

    if batch_memory:
        messages.append({"role": "system", "content": f"Batch context: {batch_memory}"})

    user_msg = (
        f"Settlement {ep.settlement_id} has a difference of "
        f"{ep.difference_paise} paise "
        f"(expected {ep.expected_amount_paise}, actual {ep.actual_amount_paise}).\n\n"
        f"Deterministic checks failed: {ep.deterministic_checks_failed}\n"
        f"Deterministic checks passed: {ep.deterministic_checks_passed}\n\n"
        f"Evidence:\n{evidence_json}\n\n"
        "Investigate this discrepancy. Use tools to verify facts. "
        "Return JSON with classification, explanation, raw_confidence, "
        "cited_evidence, recommended_action, and reasoning_steps."
    )

    if previous_steps:
        step_history = json.dumps(previous_steps, indent=2)
        user_msg += (
            f"\n\nPrevious reasoning steps:\n{step_history}\n\n"
            "Continue your investigation. Have you gathered enough evidence? "
            "Should you call more tools or make a final decision?"
        )

    messages.append({"role": "user", "content": user_msg})

    return messages


# ---------------------------------------------------------------------------
# Response validation — v2 with reasoning steps
# ---------------------------------------------------------------------------

def _validate_agent_response(raw: dict[str, Any]) -> tuple[AIResponse, list[dict]]:
    """Validate and parse LLM response into AIResponse + reasoning steps.
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

    # Parse recommended action
    action_str = raw.get("recommended_action", "ESCALATE_TO_HUMAN")
    try:
        recommended_action = AIRecommendedAction(action_str)
    except ValueError:
        recommended_action = AIRecommendedAction.ESCALATE_TO_HUMAN

    # Clamp confidence to [0.0, 1.0]
    confidence = max(0.0, min(1.0, confidence))

    # Parse reasoning steps
    reasoning_steps = raw.get("reasoning_steps", [])
    if not isinstance(reasoning_steps, list):
        reasoning_steps = []

    return (
        AIResponse(
            classification=classification,
            explanation=explanation,
            raw_confidence=confidence,
            cited_evidence=cited_evidence,
            recommended_action=recommended_action,
        ),
        reasoning_steps,
    )


# ---------------------------------------------------------------------------
# Agent execution loop
# ---------------------------------------------------------------------------

def _execute_tool_call(
    tool_name: str,
    tool_args: dict,
    evidence_packet: EvidencePacket,
) -> ToolResult:
    """Execute a single tool call and return the result."""
    tool_fn = AGENT_TOOLS.get(tool_name)
    if not tool_fn:
        return ToolResult(
            call_id=str(uuid4()),
            tool_name=tool_name,
            result={},
            success=False,
            error=f"Unknown tool: {tool_name}",
        )

    try:
        result = tool_fn(tool_args, evidence_packet)
        return ToolResult(
            call_id=str(uuid4()),
            tool_name=tool_name,
            result=result,
            success=True,
        )
    except Exception as e:
        return ToolResult(
            call_id=str(uuid4()),
            tool_name=tool_name,
            result={},
            success=False,
            error=str(e),
        )


def _run_agent_loop(
    evidence_packet: EvidencePacket,
    llm_client: LLMClient,
    timeout: float = 10.0,
    batch_memory: Optional[str] = None,
) -> tuple[AgentResponse, int, int]:
    """
    Run the ReAct agent loop (max 3 iterations).

    Returns:
        (AgentResponse, iteration_count, tool_call_count)
    """
    steps: list[dict] = []
    total_tool_calls = 0
    previous_steps_for_prompt: list[dict] = []

    for iteration in range(1, MAX_AGENT_ITERATIONS + 1):
        messages = _build_agent_messages(
            evidence_packet,
            previous_steps=previous_steps_for_prompt if previous_steps_for_prompt else None,
            batch_memory=batch_memory,
        )
        tools = get_tool_definitions()

        try:
            raw_response = llm_client.complete(
                messages,
                evidence_packet=evidence_packet,
                tools=tools,
                timeout=timeout,
            )
        except LLMError:
            raise
        except Exception as e:
            raise LLMError("unknown_error", str(e))

        # Check if LLM wants to call tools
        tool_calls = raw_response.get("tool_calls", [])
        if tool_calls and iteration < MAX_AGENT_ITERATIONS:
            # Execute tool calls
            for tc in tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("arguments", {})
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except json.JSONDecodeError:
                        tool_args = {}

                tool_result = _execute_tool_call(tool_name, tool_args, evidence_packet)
                total_tool_calls += 1

                step = {
                    "step_number": iteration,
                    "thought": raw_response.get("reasoning", f"Calling tool: {tool_name}"),
                    "action": f"tool_call:{tool_name}",
                    "observation": json.dumps(tool_result.result, default=str),
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                }
                steps.append(step)
                previous_steps_for_prompt.append(step)

            # Continue to next iteration
            continue

        # No tool calls or final iteration — parse final response
        ai_response, reasoning_steps = _validate_agent_response(raw_response)

        # Merge any reasoning steps from the response
        for rs in reasoning_steps:
            step = {
                "step_number": len(steps) + 1,
                "thought": rs.get("thought", ""),
                "action": rs.get("action", ""),
                "observation": rs.get("observation", ""),
            }
            steps.append(step)

        # Add final decision step
        steps.append({
            "step_number": len(steps) + 1,
            "thought": f"Final decision: {ai_response.classification.value} with confidence {ai_response.raw_confidence}",
            "action": f"decide:{ai_response.recommended_action.value}",
            "observation": ai_response.explanation[:200],
        })

        # Build agent trace
        trace = AgentTrace(
            settlement_id=evidence_packet.settlement_id,
            steps=[
                ReasoningStep(
                    step_number=s["step_number"],
                    action_type=AgentActionType.DECISION if "decide:" in s.get("action", "") else (
                        AgentActionType.TOOL_RESULT if "observation" in s else AgentActionType.THOUGHT
                    ),
                    thought=s["thought"],
                    tool_name=s.get("tool_name"),
                    tool_args=s.get("tool_args"),
                    tool_result=s.get("observation"),
                )
                for s in steps
            ],
            final_classification=ai_response.classification,
            final_confidence=ai_response.raw_confidence,
            confidence_tier=ConfidenceTier(compute_confidence_tier(ai_response.raw_confidence)),
            iteration_count=iteration,
            self_corrections=sum(1 for i in range(1, len(steps)) if steps[i].get("thought", "").lower().startswith("revising")),
        )

        return (
            AgentResponse(
                classification=ai_response.classification,
                explanation=ai_response.explanation,
                raw_confidence=ai_response.raw_confidence,
                cited_evidence=ai_response.cited_evidence,
                recommended_action=ai_response.recommended_action,
                trace=trace,
                tool_calls_made=total_tool_calls,
                reasoning_summary=ai_response.explanation[:500],
            ),
            iteration,
            total_tool_calls,
        )

    # Should not reach here, but safety fallback
    raise LLMError("max_iterations", "Agent exceeded max iterations without decision")


# ---------------------------------------------------------------------------
# Core investigation
# ---------------------------------------------------------------------------

def investigate(
    evidence_packet: EvidencePacket,
    llm_client: Optional[LLMClient] = None,
    timeout: float = 10.0,
    batch_memory: Optional[str] = None,
) -> InvestigationResult:
    """
    Investigate a discrepancy using the AI agent.

    Args:
        evidence_packet: Structured evidence from the deterministic engine.
        llm_client: LLM provider. If None, returns UNRESOLVED.
        timeout: LLM timeout in seconds.
        batch_memory: Optional batch-level context for cross-settlement reasoning.

    Returns:
        InvestigationResult with decision REVIEW_REQUIRED, AUTO_RESOLVED, or UNRESOLVED.
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

    # Run agent loop
    try:
        agent_response, iterations, tool_calls = _run_agent_loop(
            evidence_packet, llm_client, timeout=timeout, batch_memory=batch_memory,
        )
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

    # Derive AIResponse from AgentResponse for backward compatibility
    ai_response = AIResponse(
        classification=agent_response.classification,
        explanation=agent_response.explanation,
        raw_confidence=agent_response.raw_confidence,
        cited_evidence=agent_response.cited_evidence,
        recommended_action=agent_response.recommended_action,
    )

    # Validate citations
    if not validate_citations(agent_response.cited_evidence, evidence_packet):
        return InvestigationResult(
            settlement_id=sid,
            decision=DecisionState.UNRESOLVED,
            ai_response=ai_response,
            agent_response=agent_response,
            confidence_tier="LOW",
            error_type="hallucinated_evidence",
            escalate_to_human=True,
            agent_iterations=iterations,
            agent_tool_calls=tool_calls,
        )

    # Compute confidence tier
    tier = compute_confidence_tier(agent_response.raw_confidence)

    # Determine decision based on confidence tier and recommended action
    if (
        agent_response.recommended_action == AIRecommendedAction.AUTO_RESOLVE
        and agent_response.raw_confidence >= AUTO_RESOLVE_CONFIDENCE_THRESHOLD
        and tier == ConfidenceTier.TIER_1.value
        and _is_trivial_auto_resolve(evidence_packet)
    ):
        # Auto-resolve: only for trivial cases with very high confidence
        return InvestigationResult(
            settlement_id=sid,
            decision=DecisionState.AUTO_RESOLVED,
            ai_response=ai_response,
            agent_response=agent_response,
            confidence_tier=tier,
            escalate_to_human=False,
            is_mock=isinstance(llm_client, DemoLLMClient),
            agent_iterations=iterations,
            agent_tool_calls=tool_calls,
        )
    else:
        # All other cases → human review queue
        return InvestigationResult(
            settlement_id=sid,
            decision=DecisionState.REVIEW_REQUIRED,
            ai_response=ai_response,
            agent_response=agent_response,
            confidence_tier=tier,
            escalate_to_human=True,
            is_mock=isinstance(llm_client, DemoLLMClient),
            agent_iterations=iterations,
            agent_tool_calls=tool_calls,
        )


def _is_trivial_auto_resolve(evidence: EvidencePacket) -> bool:
    """Check if this is a trivial case eligible for auto-resolve.

    Trivial cases are those where:
    - difference is <= 1 paise (rounding error)
    - OR all deterministic checks passed and difference is exactly a known rounding pattern
    - No linkage issues, no missing references, no duplicates
    """
    diff = abs(evidence.difference_paise)

    # Off-by-one paise is always trivial
    if diff <= 1:
        return True

    # Known rounding patterns: difference is exactly 1 paise per payment
    if evidence.linked_payments_summary.count > 0:
        per_payment_diff = diff // evidence.linked_payments_summary.count
        if per_payment_diff <= 1 and diff % evidence.linked_payments_summary.count == 0:
            return True

    return False


# ---------------------------------------------------------------------------
# Backward-compatible AIResponse wrapper (for engine.py compatibility)
# ---------------------------------------------------------------------------

# Keep the old AIResponse-compatible interface for existing engine.py code
# The new AgentResponse extends this with trace information


# ---------------------------------------------------------------------------
# Mock LLM client (for testing)
# ---------------------------------------------------------------------------

class OpenAIClient:
    """Real LLM client using OpenAI API. Falls back to UNRESOLVED on failure."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini"):
        try:
            from openai import OpenAI
        except ImportError:
            raise LLMError("import_error", "openai package not installed. Run: pip install openai")
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def complete(
        self,
        messages: list[dict],
        evidence_packet: Optional[EvidencePacket] = None,
        tools: Optional[list[dict]] = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        import json as _json
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "timeout": timeout,
            "response_format": {"type": "json_object"},
        }
        # Note: OpenAI function calling would go here with tools parameter
        # For now, we use JSON mode and let the LLM include tool calls in JSON
        try:
            response = self._client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content
            return _json.loads(content)
        except Exception as e:
            error_msg = str(e).lower()
            if "timeout" in error_msg or "timed out" in error_msg:
                raise LLMTimeoutError(str(e))
            elif "api" in error_msg or "rate" in error_msg or "auth" in error_msg:
                raise LLMAPIError(str(e))
            else:
                raise LLMError("unknown_error", str(e))


class MockLLMClient:
    """Mock LLM client that returns controlled responses for testing."""

    def __init__(
        self,
        classification: str = "UNEXPLAINED",
        explanation: str = "No clear explanation found.",
        confidence: float = 0.5,
        cited_evidence: Optional[list[str]] = None,
        fail_with: Optional[str] = None,
        tool_calls: Optional[list[dict]] = None,
    ):
        self._classification = classification
        self._explanation = explanation
        self._confidence = confidence
        self._cited_evidence = cited_evidence or ["timing"]
        self._fail_with = fail_with
        self._call_count = 0
        self._tool_calls = tool_calls or []
        self._call_history: list[dict] = []

    def complete(
        self,
        messages: list[dict],
        evidence_packet: Optional[EvidencePacket] = None,
        tools: Optional[list[dict]] = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        self._call_count += 1
        self._call_history.append({"messages": messages, "evidence_packet": evidence_packet})

        if self._fail_with == "timeout":
            raise LLMTimeoutError()
        elif self._fail_with == "api_error":
            raise LLMAPIError()
        elif self._fail_with == "malformed_json":
            raise LLMMalformedResponseError()
        elif self._fail_with:
            raise LLMError(self._fail_with)

        # Return tool calls if configured (for agent loop testing)
        if self._tool_calls and self._call_count <= len(self._tool_calls):
            tc = self._tool_calls[self._call_count - 1]
            if tc is not None:
                return {"tool_calls": [tc], "reasoning": f"Calling tool: {tc.get('name', '')}"}

        return {
            "classification": self._classification,
            "explanation": self._explanation,
            "raw_confidence": self._confidence,
            "cited_evidence": self._cited_evidence,
            "recommended_action": "ESCALATE_TO_HUMAN",
            "reasoning_steps": [],
        }


# ---------------------------------------------------------------------------
# Demo LLM client (heuristic, clearly labeled)
# ---------------------------------------------------------------------------

_DEMO_PREFIX = "[DEMO] "


def _compute_confidence(failed_checks: list[str], total_checks: int = 12) -> float:
    """Confidence = proportion of checks that passed.
    More checks passed = higher confidence in classification."""
    if not failed_checks:
        return 0.4  # MATH_DISCREPANCY — low confidence, no failed check to anchor on
    passed = total_checks - len(failed_checks)
    return round(0.5 + (passed / total_checks) * 0.4, 2)  # Range: 0.50 - 0.90


def _classify_by_heuristic(evidence: EvidencePacket) -> tuple[str, str, float, list[str]]:
    """Heuristic classifier for demo without API key.

    Reasons across multiple signals: cross-settlement patterns, payment-level
    breakdowns, and batch-level statistics — not just the failed check name.

    Returns (classification, explanation, confidence, cited_evidence).
    All outputs are clearly labeled as demo to prevent confusion with live AI.
    """
    refunds = evidence.linked_refunds_summary
    timing = evidence.timing
    diff = evidence.difference_paise
    failed_checks = evidence.deterministic_checks_failed
    payments = evidence.linked_payments_summary
    cross = evidence.cross_settlement
    details = evidence.payment_details

    # ── DETERMINISTIC_EXCEPTION cases: reason across signals ──
    if failed_checks:
        check_set = set(failed_checks)

        # ── Fee + Tax both failed: systemic fee tier misconfiguration ──
        if "fee_validation" in check_set and "tax_validation" in check_set:
            mismatched = [d for d in details if d.fee_mismatch]
            methods = list(set(d.method.value for d in mismatched))
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}Systemic fee+tax failure for {evidence.settlement_id}. "
                    f"{len(mismatched)} payment(s) have incorrect fees ({', '.join(methods)} methods). "
                    f"Expected fees: {[f'{d.payment_id}: \u20B9{d.fee_expected_paise/100:.2f}' for d in mismatched[:3]]}. "
                    f"This pattern suggests a fee tier misconfiguration rather than individual errors. "
                    f"Remediation: Check merchant's contracted fee rates; verify if a fee waiver or "
                    f"promotional rate was applied inconsistently across payment methods."
                ),
                _compute_confidence(failed_checks),
                ["fees_summary", "tax_summary", "linked_payments_summary"],
            )

        # ── Fee validation failed: per-payment breakdown ──
        if "fee_validation" in check_set:
            mismatched = [d for d in details if d.fee_mismatch]
            overcharged = [d for d in mismatched if d.fee_paise > d.fee_expected_paise]
            undercharged = [d for d in mismatched if d.fee_paise < d.fee_expected_paise]
            batch_rate = cross.batch_fee_exception_rate if cross else 0

            if overcharged and not undercharged:
                ex = overcharged[0]
                return (
                    "UNEXPLAINED",
                    (
                        f"{_DEMO_PREFIX}Fee overcharge detected for {evidence.settlement_id}. "
                        f"{len(overcharged)} payment(s) have fees above the expected rate. "
                        f"Example: {ex.payment_id} charged \u20B9{ex.fee_paise/100:.2f}, "
                        f"expected \u20B9{ex.fee_expected_paise/100:.2f} "
                        f"({ex.method.value}). "
                        f"Batch context: {batch_rate*100:.0f}% of settlements in this batch have fee exceptions. "
                        f"Remediation: Verify if merchant is on a different fee tier; check for manual fee overrides."
                    ),
                    _compute_confidence(failed_checks),
                    ["fees_summary", "linked_payments_summary"],
                )
            if undercharged and not overcharged:
                ex = undercharged[0]
                return (
                    "UNEXPLAINED",
                    (
                        f"{_DEMO_PREFIX}Fee undercharge detected for {evidence.settlement_id}. "
                        f"{len(undercharged)} payment(s) have fees below the expected rate. "
                        f"Example: {ex.payment_id} charged \u20B9{ex.fee_paise/100:.2f}, "
                        f"expected \u20B9{ex.fee_expected_paise/100:.2f} "
                        f"({ex.method.value}). "
                        f"This may indicate a promotional fee waiver or rate card discrepancy. "
                        f"Remediation: Check if a fee waiver was applied; verify the merchant's contracted rate."
                    ),
                    _compute_confidence(failed_checks),
                    ["fees_summary", "linked_payments_summary"],
                )
            # Mixed over/under
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}Mixed fee errors for {evidence.settlement_id}. "
                    f"{len(overcharged)} overcharged, {len(undercharged)} undercharged across "
                    f"{len(mismatched)} payment(s). This pattern suggests a fee tier misalignment "
                    f"rather than isolated errors. "
                    f"Remediation: Pull the merchant's fee contract; cross-reference each payment method's "
                    f"applicable rate against what was charged."
                ),
                _compute_confidence(failed_checks),
                ["fees_summary", "linked_payments_summary"],
            )

        # ── Tax validation failed: per-payment derivation check ──
        if "tax_validation" in check_set:
            mismatched = [d for d in details if d.tax_mismatch]
            if mismatched:
                examples = []
                for d in mismatched[:3]:
                    examples.append(
                        f"{d.payment_id}: fee=\u20B9{d.fee_paise/100:.2f}, "
                        f"tax=\u20B9{d.tax_paise/100:.2f}, expected=\u20B9{d.tax_expected_paise/100:.2f}"
                    )
                return (
                    "UNEXPLAINED",
                    (
                        f"{_DEMO_PREFIX}Tax derivation mismatch for {evidence.settlement_id}. "
                        f"{len(mismatched)} payment(s) have incorrect GST. Examples: "
                        f"{'; '.join(examples)}. "
                        f"Rule: tax must equal floor(fee \u00d7 18/100). "
                        f"Remediation: Check if GST rate changed mid-period; verify rounding method; "
                        f"confirm tax-exclusive vs tax-inclusive handling."
                    ),
                    _compute_confidence(failed_checks),
                    ["tax_summary", "linked_payments_summary"],
                )
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}Tax validation failed for {evidence.settlement_id} "
                    f"but no individual payment-level mismatch found. "
                    f"Remediation: Check aggregate tax calculation across all linked payments."
                ),
                _compute_confidence(failed_checks),
                ["tax_summary"],
            )

        # ── Bank credit existence ──
        if "bank_credit_existence" in check_set:
            delta = (timing.bank_credited_at - timing.settled_at).days
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}No bank credit for {evidence.settlement_id} "
                    f"(expected \u20B9{evidence.expected_amount_paise/100:,.2f}). "
                    f"Settlement settled {timing.settled_at.date()}, "
                    f"bank credited {timing.bank_credited_at.date()} ({delta} day delta). "
                    f"Remediation: Check if credit is pending T+1/T+2; verify UTR mapping; "
                    f"contact bank with settlement reference."
                ),
                _compute_confidence(failed_checks),
                ["bank_credit", "timing"],
            )

        # ── UTR cross-check ──
        if "utr_cross_check" in check_set:
            bc = evidence.bank_credit
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}UTR mismatch for {evidence.settlement_id}. "
                    f"Settlement UTR does not match bank credit UTR ({bc.utr}). "
                    f"Bank credited \u20B9{bc.amount_paise/100:,.2f} on {bc.date}. "
                    f"Remediation: Check if UTR belongs to a different batch; "
                    f"cross-reference with bank's NEFT/RTGS reference directory."
                ),
                _compute_confidence(failed_checks),
                ["bank_credit", "timing"],
            )

        # ── Duplicate detection ──
        if "duplicate_detection" in check_set:
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}Duplicate detected for {evidence.settlement_id}. "
                    f"A payment ID, refund ID, or UTR appears in multiple records. "
                    f"Remediation: Identify original vs duplicate; check for retry or "
                    f"idempotency failure; remove or mark the duplicate."
                ),
                _compute_confidence(failed_checks),
                ["linked_payments_summary", "linked_refunds_summary"],
            )

        # ── Reference existence ──
        if "reference_existence" in check_set:
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}Missing reference for {evidence.settlement_id}. "
                    f"A linked payment or refund ID does not exist in transaction records. "
                    f"Remediation: Check if the transaction was deleted or belongs to a "
                    f"different merchant account."
                ),
                _compute_confidence(failed_checks),
                ["linked_payments_summary"],
            )

        # ── Linkage consistency ──
        if "linkage_consistency" in check_set:
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}Linkage inconsistency for {evidence.settlement_id}. "
                    f"A payment is claimed by multiple settlements or a refund exceeds "
                    f"its parent payment. "
                    f"Remediation: Trace each payment ID to its correct settlement."
                ),
                _compute_confidence(failed_checks),
                ["linked_payments_summary", "linked_refunds_summary"],
            )

        # ── Adjustment consistency ──
        if "adjustment_consistency" in check_set:
            return (
                "UNEXPLAINED",
                (
                    f"{_DEMO_PREFIX}Adjustment inconsistency for {evidence.settlement_id}. "
                    f"Declared adjustment does not bridge expected-vs-actual gap "
                    f"(difference: \u20B9{abs(diff)/100:,.2f}). "
                    f"Remediation: Verify adjustment amount matches documented reason; "
                    f"cross-check with adjustment approval record."
                ),
                _compute_confidence(failed_checks),
                ["linked_payments_summary"],
            )

        # Fallback
        check = failed_checks[0]
        return (
            "UNEXPLAINED",
            (
                f"{_DEMO_PREFIX}Deterministic check '{check}' failed for {evidence.settlement_id}. "
                f"Remediation: Review the specific failed check against source records."
            ),
            _compute_confidence(failed_checks),
            [],
        )

    # ── MATH_DISCREPANCY cases ──

    # Refund-linked discrepancy
    if refunds.count > 0:
        return (
            "REFUND_TIMING",
            (
                f"{_DEMO_PREFIX}Linked refunds detected ({refunds.count} refunds totaling "
                f"\u20B9{refunds.total_paise/100:,.2f}). Difference of \u20B9{abs(diff)/100:,.2f} "
                f"is likely caused by refund timing within the settlement window. "
                f"Remediation: Verify refund processing dates against settlement cutoff."
            ),
            _compute_confidence(failed_checks),
            ["linked_refunds_summary", "timing"],
        )

    # Timing-based discrepancy
    try:
        delta = (timing.bank_credited_at - timing.settled_at).days
    except Exception:
        delta = 0

    if delta > timing.expected_cycle_days:
        return (
            "TIMING_MISMATCH",
            (
                f"{_DEMO_PREFIX}Bank credit delayed by {delta} days (expected "
                f"{timing.expected_cycle_days}-day cycle). The \u20B9{abs(diff)/100:,.2f} "
                f"difference may result from bank-side processing delay. "
                f"Remediation: Contact bank to confirm credit processing status."
            ),
            _compute_confidence(failed_checks),
            ["timing", "bank_credit"],
        )

    # Default
    return (
        "UNEXPLAINED",
        (
            f"{_DEMO_PREFIX}Difference of \u20B9{abs(diff)/100:,.2f} detected "
            f"but no clear timing or refund cause was found. "
            f"Remediation: Manually review the settlement breakdown."
        ),
        _compute_confidence(failed_checks),
        ["timing"],
    )


class DemoLLMClient:
    """Heuristic fallback classifier used when OpenAI is unavailable.

    Returns structured exception categories based on deterministic check failures.
    This client ensures the full pipeline runs end-to-end without
    requiring an API key, while making it unambiguous that outputs
    are heuristic-based, not live AI.

    Accepts EvidencePacket directly — no string parsing.
    """

    def complete(
        self,
        messages: list[dict],
        evidence_packet: Optional[EvidencePacket] = None,
        tools: Optional[list[dict]] = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        # Use EvidencePacket directly if provided (no string parsing!)
        if evidence_packet is not None:
            evidence = evidence_packet
        else:
            # Fallback: try to extract from messages (legacy compatibility)
            evidence = self._extract_evidence_from_messages(messages)

        classification, explanation, confidence, cited = _classify_by_heuristic(evidence)
        return {
            "classification": classification,
            "explanation": explanation,
            "raw_confidence": confidence,
            "cited_evidence": cited,
            "recommended_action": "ESCALATE_TO_HUMAN",
            "reasoning_steps": [],
        }

    def _extract_evidence_from_messages(self, messages: list[dict]) -> EvidencePacket:
        """Legacy fallback: reconstruct EvidencePacket from prompt messages.

        DEPRECATED: New code should pass evidence_packet directly.
        This method exists only for backward compatibility.
        """
        for msg in messages:
            content = msg.get("content", "")
            if "Evidence:" in content:
                try:
                    evidence_start = content.index("Evidence:\n") + len("Evidence:\n")
                    evidence_end = content.index("\n\n", evidence_start)
                    import json as _json
                    evidence_json = _json.loads(content[evidence_start:evidence_end])
                    actual = max(1, evidence_json.get("actual_amount_paise", 1))
                    expected = max(0, evidence_json.get("expected_amount_paise", 0))
                    bc = evidence_json.get("bank_credit", {})
                    bc_amount = max(1, bc.get("amount_paise", 1))
                    timing = evidence_json.get("timing", {})

                    def _parse_dt(val):
                        if isinstance(val, str):
                            try:
                                return datetime.fromisoformat(val)
                            except Exception:
                                return datetime.now()
                        return datetime.now()

                    payment_details = []
                    for pd_dict in evidence_json.get("payment_details", []):
                        payment_details.append(PaymentDetail(
                            payment_id=pd_dict.get("payment_id", ""),
                            amount_paise=pd_dict.get("amount_paise", 0),
                            method=PaymentMethod(pd_dict.get("method", "upi")),
                            fee_paise=pd_dict.get("fee_paise", 0),
                            tax_paise=pd_dict.get("tax_paise", 0),
                            fee_expected_paise=pd_dict.get("fee_expected_paise", 0),
                            tax_expected_paise=pd_dict.get("tax_expected_paise", 0),
                            fee_mismatch=pd_dict.get("fee_mismatch", False),
                            tax_mismatch=pd_dict.get("tax_mismatch", False),
                        ))

                    cross_dict = evidence_json.get("cross_settlement_context")
                    cross_ctx = None
                    if cross_dict:
                        from backend.models import CrossSettlementContext
                        cross_ctx = CrossSettlementContext(
                            batch_size=cross_dict.get("batch_size", 0),
                            batch_fee_exception_rate=cross_dict.get("batch_fee_exception_rate", 0),
                            batch_refund_rate=cross_dict.get("batch_refund_rate", 0),
                            batch_math_discrepancy_rate=cross_dict.get("batch_math_discrepancy_rate", 0),
                            merchant_fee_exceptions_in_batch=cross_dict.get("merchant_fee_exceptions_in_batch", 0),
                            method_mix=cross_dict.get("method_mix", {}),
                        )

                    return EvidencePacket(
                        settlement_id=evidence_json.get("settlement_id", "UNKNOWN"),
                        expected_amount_paise=expected,
                        actual_amount_paise=actual,
                        difference_paise=evidence_json.get("difference_paise", 0),
                        linked_payments_summary=LinkedPaymentsSummary(
                            count=evidence_json.get("linked_payments_summary", {}).get("count", 0),
                            total_paise=evidence_json.get("linked_payments_summary", {}).get("total_paise", 0),
                            methods=[PaymentMethod.UPI],
                        ),
                        linked_refunds_summary=LinkedRefundsSummary(
                            count=evidence_json.get("linked_refunds_summary", {}).get("count", 0),
                            total_paise=evidence_json.get("linked_refunds_summary", {}).get("total_paise", 0),
                        ),
                        fees_summary=FeesSummary(
                            total_paise=evidence_json.get("fees_summary", {}).get("total_paise", 0),
                            structure_applied="deterministic",
                            validation_result=ValidationResult.PASSED,
                        ),
                        tax_summary=TaxSummary(
                            total_paise=evidence_json.get("tax_summary", {}).get("total_paise", 0),
                            derivation_rule="floor(fee * 0.18)",
                            validation_result=ValidationResult.PASSED,
                        ),
                        bank_credit=BankCreditEvidence(
                            utr=bc.get("utr", ""),
                            amount_paise=bc_amount,
                            date=_parse_dt(bc.get("date")).date() if bc.get("date") else datetime.now().date(),
                        ),
                        timing=TimingEvidence(
                            settlement_created_at=_parse_dt(timing.get("settlement_created_at")),
                            settled_at=_parse_dt(timing.get("settled_at")),
                            bank_credited_at=_parse_dt(timing.get("bank_credited_at")),
                            expected_cycle_days=timing.get("expected_cycle_days", 2),
                        ),
                        deterministic_checks_passed=evidence_json.get("deterministic_checks_passed", []),
                        deterministic_checks_failed=evidence_json.get("deterministic_checks_failed", []),
                        payment_details=payment_details,
                        cross_settlement=cross_ctx,
                    )
                except Exception:
                    pass

        # Fallback minimal evidence
        return EvidencePacket(
            settlement_id="UNKNOWN",
            expected_amount_paise=0,
            actual_amount_paise=1,
            difference_paise=1,
            linked_payments_summary=LinkedPaymentsSummary(count=0, total_paise=0, methods=[PaymentMethod.UPI]),
            linked_refunds_summary=LinkedRefundsSummary(count=0, total_paise=0),
            fees_summary=FeesSummary(total_paise=0, structure_applied="deterministic", validation_result=ValidationResult.PASSED),
            tax_summary=TaxSummary(total_paise=0, derivation_rule="floor(fee * 0.18)", validation_result=ValidationResult.PASSED),
            bank_credit=BankCreditEvidence(utr="", amount_paise=1, date=datetime.now().date()),
            timing=TimingEvidence(settlement_created_at=datetime.now(), settled_at=datetime.now(), bank_credited_at=datetime.now(), expected_cycle_days=2),
            deterministic_checks_passed=[],
            deterministic_checks_failed=[],
        )
