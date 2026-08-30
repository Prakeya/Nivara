"""
Shared test mocks for Nivara.

Provides MockLLMClient for unit and integration tests.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.ai_investigator import (
    EvidencePacket,
    LLMTimeoutError,
    LLMAPIError,
    LLMMalformedResponseError,
    LLMError,
)


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
