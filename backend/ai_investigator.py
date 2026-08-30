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
    from backend.ai_investigator import investigate, OpenAIClient
    client = OpenAIClient(api_key="sk-...")
    result = investigate(evidence_packet, llm_client=client)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional, Protocol
from uuid import uuid4

logger = logging.getLogger("nivara.ai_investigator")

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


def _sanitize_for_prompt(value: str, max_length: int = 100) -> str:
    """Sanitize user-controlled values before inserting into LLM prompts.

    Strips control characters, escapes prompt delimiters, and limits length
    to reduce prompt injection risk.
    """
    import re as _re
    if not isinstance(value, str):
        value = str(value)
    value = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', value)
    value = value.replace("```", "'''")
    value = value.replace("<|im_start|>", "<im_start>")
    value = value.replace("<|im_end|>", "<im_end>")
    value = value.replace("SYSTEM:", "SYSTEM ")
    value = value.replace("Ignore all", "Ignore-all")
    if len(value) > max_length:
        value = value[:max_length] + "..."
    return value


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
        f"Settlement {_sanitize_for_prompt(ep.settlement_id)} has a difference of "
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

    # Determine decision based on confidence tier and trivial case check
    # Auto-resolve: when all checks passed, difference is trivial (<=1 paise),
    # and confidence is very high. The LLM's recommended_action is not required
    # to be AUTO_RESOLVE — the engine decides based on evidence, not LLM opinion.
    if (
        agent_response.raw_confidence >= AUTO_RESOLVE_CONFIDENCE_THRESHOLD
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
            is_mock=False,
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
            is_mock=False,
            agent_iterations=iterations,
            agent_tool_calls=tool_calls,
        )


def _is_trivial_auto_resolve(evidence: EvidencePacket) -> bool:
    """Check if this is a trivial case eligible for auto-resolve.

    Trivial cases are those where:
    - All deterministic checks passed (no DET-EXCEPTION)
    - difference is <= 1 paise (rounding error)
    - OR all deterministic checks passed and difference is exactly a known rounding pattern
    - No linkage issues, no missing references, no duplicates
    """
    # Never auto-resolve if any deterministic checks failed
    if evidence.deterministic_checks_failed:
        return False

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


# ---------------------------------------------------------------------------
# investigate_v2: New architecture using EvidencePacketV2
# ---------------------------------------------------------------------------

def investigate_v2(
    evidence_packet_v2: "EvidencePacketV2",
    expected_amount_paise: int,
    actual_amount_paise: int,
    difference_paise: int,
) -> Optional[AIResponse]:
    """
    Investigate a MATH_DISCREPANCY using EvidencePacketV2.

    This is the new architecture function. It:
    1. Builds a prompt from EvidencePacketV2
    2. Calls LLM via fallback chain (OpenAI → Anthropic → Local)
    3. Validates response via AI Validator
    4. Returns AIResponse or None (no heuristic fallback)

    Requires OPENAI_API_KEY or ANTHROPIC_API_KEY environment variables.
    """
    from backend.evidence_packet import EvidencePacketV2
    from backend.fallback_chain import call_with_fallback, ProviderConfig
    from backend.ai_validator import validate_ai_response
    from backend.prompt_registry import get_prompt, get_prompt_version

    # Build prompt from evidence packet
    system_prompt = get_prompt("v1/system")
    evidence_text = evidence_packet_v2.serialize_for_prompt()
    user_prompt = (
        f"Settlement: {evidence_packet_v2.settlement_id}\n"
        f"Expected amount: {expected_amount_paise} paise\n"
        f"Actual amount: {actual_amount_paise} paise\n"
        f"Difference: {difference_paise} paise\n\n"
        f"Evidence:\n{evidence_text}\n\n"
        f"Analyze the discrepancy and return JSON with classification, explanation, confidence, and cited_evidence."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # Call LLM via fallback chain
    fallback_result = call_with_fallback(messages=messages)
    if not fallback_result.success:
        logger.warning("All LLM providers failed for %s", evidence_packet_v2.settlement_id)
        return None

    # Validate response
    prompt_version = get_prompt_version("v1/system")
    validation = validate_ai_response(
        raw_response=fallback_result.response,
        evidence_packet=evidence_packet_v2,
        provider=fallback_result.provider,
        prompt_version=prompt_version,
        latency_ms=fallback_result.latency_ms,
    )

    if validation.ai_response is None:
        logger.warning(
            "AI validation failed for %s: %s",
            evidence_packet_v2.settlement_id,
            validation.error,
        )
        return None

    return validation.ai_response

