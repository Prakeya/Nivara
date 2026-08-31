"""
Cost tracking per settlement: tokens in/out, provider, INR cost.

Tracks LLM API usage and cost for each settlement investigation.
Pricing is configurable via environment or defaults to OpenAI gpt-4o-mini rates.
"""

from __future__ import annotations

import os
from typing import Any
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LLMUsage:
    """LLM API usage for a single call."""
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_inr: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class SettlementCost:
    """Aggregated cost for a single settlement investigation."""
    settlement_id: str
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_inr: float = 0.0
    calls: list[LLMUsage] = field(default_factory=list)
    total_latency_ms: float = 0.0

    def add_usage(self, usage: LLMUsage) -> None:
        self.calls.append(usage)
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        self.total_cost_inr += usage.cost_inr
        self.total_latency_ms += usage.latency_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "settlement_id": self.settlement_id,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_inr": round(self.total_cost_inr, 4),
            "num_calls": len(self.calls),
            "total_latency_ms": round(self.total_latency_ms, 2),
        }


# Default pricing (INR per 1K tokens) — gpt-4o-mini
PRICING = {
    "gpt-4o-mini": {"input_per_1k": 0.0137, "output_per_1k": 0.0548},
    "gpt-4o": {"input_per_1k": 1.83, "output_per_1k": 7.33},
    "gpt-3.5-turbo": {"input_per_1k": 0.0035, "output_per_1k": 0.0070},
}


def compute_cost_inr(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute cost in INR for given token counts."""
    rates = PRICING.get(model, PRICING["gpt-4o-mini"])
    input_cost = (input_tokens / 1000) * rates["input_per_1k"]
    output_cost = (output_tokens / 1000) * rates["output_per_1k"]
    return round(input_cost + output_cost, 6)


class CostTracker:
    """Track costs across all settlements in a batch."""

    def __init__(self) -> None:
        self._settlements: dict[str, SettlementCost] = {}
        self._total_cost_inr: float = 0.0
        self._total_tokens: int = 0

    def record(
        self,
        settlement_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float = 0.0,
    ) -> SettlementCost:
        """Record LLM usage for a settlement."""
        if settlement_id not in self._settlements:
            self._settlements[settlement_id] = SettlementCost(settlement_id=settlement_id)

        cost = compute_cost_inr(model, input_tokens, output_tokens)
        usage = LLMUsage(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_inr=cost,
        )
        sc = self._settlements[settlement_id]
        sc.add_usage(usage)
        self._total_cost_inr += cost
        self._total_tokens += input_tokens + output_tokens
        return sc

    def get_settlement_cost(self, settlement_id: str) -> Optional[SettlementCost]:
        return self._settlements.get(settlement_id)

    def summary(self) -> dict[str, Any]:
        return {
            "total_settlements": len(self._settlements),
            "total_cost_inr": round(self._total_cost_inr, 4),
            "total_tokens": self._total_tokens,
            "avg_cost_per_settlement": round(
                self._total_cost_inr / max(len(self._settlements), 1), 4
            ),
        }

    def all_settlements(self) -> list[dict[str, Any]]:
        return [sc.to_dict() for sc in self._settlements.values()]
