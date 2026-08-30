"""
Fallback Chain: Groq primary (llama-3.1-70b-versatile) → Groq fallback
(llama-3.1-8b-instant) → UNRESOLVED.

Rules:
- Pre-flight via GroqRateLimiter (requests/min, tokens/min, daily budget).
- Try the 70B model first with a 15s timeout.
- On failure (timeout, rate limit, daily limit, API error, unparsable JSON)
  retry ONCE with the 8B model.
- Second failure → success=False → UNRESOLVED → human review.
- No third-party providers. No heuristic fallback (never guess conclusions).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.circuit_breaker import get_breaker
from backend.groq_client import (
    DEFAULT_MODEL,
    FALLBACK_MODEL,
    GroqClient,
    GroqError,
    GroqRateLimiter,
    parse_llm_json,
)

logger = logging.getLogger("nivara.fallback_chain")

# Groq primary → fallback model order
PRIMARY_MODEL = DEFAULT_MODEL
SECONDARY_MODEL = FALLBACK_MODEL

DEFAULT_TIMEOUT = 15.0


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider (Groq-first)."""

    name: str = "groq"
    api_key_env: str = "GROQ_API_KEY"
    timeout: float = DEFAULT_TIMEOUT


@dataclass
class FallbackResult:
    """Result of a fallback chain call."""

    provider: str = "none"
    response: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    success: bool = False
    error: str | None = None
    model: str | None = None


DEFAULT_PROVIDERS = [ProviderConfig(name="groq", api_key_env="GROQ_API_KEY")]


def _build_ai_response(raw: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    """
    Assemble an ai_validator-compatible response dict from a Groq call.

    Keeps ai_validator as the single source of truth: classification/explanation/
    confidence/cited_evidence at top level + OpenAI-style usage block for tokens.
    """
    return {
        "classification": parsed.get("classification"),
        "explanation": parsed.get("explanation"),
        "confidence": parsed.get("confidence"),
        "cited_evidence": parsed.get("cited_evidence", []),
        "usage": {
            "prompt_tokens": raw.get("tokens_in", 0),
            "completion_tokens": raw.get("tokens_out", 0),
        },
    }


class GroqFallbackChain:
    """Groq-first chain: 70B primary → 8B fallback → UNRESOLVED."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        rate_limiter: Optional[GroqRateLimiter] = None,
        client: Optional[GroqClient] = None,
    ) -> None:
        api_key = api_key or os.environ.get("GROQ_API_KEY", "") or ""
        if not api_key:
            logger.warning("%s not set — Groq calls will fail (deterministic engine unaffected)", "GROQ_API_KEY")
        self._timeout = timeout
        self._rate_limiter = rate_limiter or GroqRateLimiter()
        self._client = client or GroqClient(
            api_key=api_key,
            rate_limiter=self._rate_limiter,
        )

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        primary_model: Optional[str] = None,
    ) -> FallbackResult:
        """
        Run the Groq fallback chain.

        Attempts primary_model (default: 70B) then the alternate model (once
        each). Returns FallbackResult with success=True and a response dict
        compatible with ai_validator, or success=False on full failure.
        """
        if tools:
            # Groq JSON mode is driven by the prompt; object-mode JSON works too.
            logger.info("tools passed to Groq chain (ignored — JSON via prompt)")
        chain_start = time.monotonic()

        primary = primary_model or PRIMARY_MODEL
        fallback = SECONDARY_MODEL if primary == PRIMARY_MODEL else PRIMARY_MODEL

        for model in (primary, fallback):
            breaker_name = f"groq::{model}"
            breaker = get_breaker(breaker_name)
            if not breaker.allow_request():
                logger.info("Skipping %s — circuit breaker OPEN", model)
                continue

            attempt_start = time.monotonic()
            try:
                raw = self._client.complete(messages, timeout=self._timeout, model=model)
            except GroqError as exc:
                breaker.record_failure()
                logger.warning("Groq %s failed: %s", model, exc)
                continue

            parsed = parse_llm_json(raw.get("text", ""))
            if parsed is None:
                breaker.record_failure()
                logger.warning("Groq %s returned unparsable JSON", model)
                continue

            breaker.record_success()
            elapsed_ms = int((time.monotonic() - chain_start) * 1000)
            logger.info("Groq succeeded via %s in %dms", model, int((time.monotonic() - attempt_start) * 1000))
            return FallbackResult(
                provider="groq",
                response=_build_ai_response(raw, parsed),
                latency_ms=elapsed_ms,
                success=True,
                model=model,
            )

        logger.warning("Groq fallback chain exhausted (70B and 8B)")
        return FallbackResult(
            provider="none",
            success=False,
            error="Groq models failed (70B and 8B)",
        )


def call_with_fallback(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    providers: list[ProviderConfig] | None = None,
    primary_model: Optional[str] = None,
) -> FallbackResult:
    """
    Public entry point: run the Groq fallback chain.

    Kept as the module-level function for backward compatibility. Returns the
    same FallbackResult shape as the pre-Groq implementation.
    """
    config = (providers or DEFAULT_PROVIDERS)[0]
    chain = GroqFallbackChain(
        api_key=os.environ.get(config.api_key_env, ""),
        timeout=config.timeout,
    )
    return chain.call(messages, tools=tools, primary_model=primary_model)