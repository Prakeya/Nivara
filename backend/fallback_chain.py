"""
Fallback Chain: OpenAI → Anthropic → Local provider fallback.

Rules:
- Try providers in order. First success wins.
- Uses existing circuit breaker per provider.
- If all providers fail → return None (no heuristic fallback).
- Exponential backoff between retries.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.circuit_breaker import get_breaker, CircuitState

logger = logging.getLogger("nivara.fallback_chain")


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider."""

    name: str
    api_key_env: str
    base_url: str | None = None
    timeout: float = 30.0
    max_retries: int = 2
    base_delay: float = 1.0
    max_delay: float = 30.0


# Default provider order
DEFAULT_PROVIDERS = [
    ProviderConfig(name="openai", api_key_env="OPENAI_API_KEY"),
    ProviderConfig(name="anthropic", api_key_env="ANTHROPIC_API_KEY"),
    ProviderConfig(name="local", api_key_env="", base_url="http://localhost:11434"),
]


@dataclass
class FallbackResult:
    """Result of a fallback chain call."""

    provider: str = "none"
    response: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    success: bool = False
    error: str | None = None


def _call_provider(
    provider: ProviderConfig,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Call a single LLM provider. Raises on failure.

    This is a thin wrapper. Actual HTTP calls are provider-specific.
    For now, we simulate with a generic call.
    """
    api_key = os.environ.get(provider.api_key_env, "")
    if not api_key and provider.name != "local":
        raise ValueError(f"No API key for {provider.name} (env: {provider.api_key_env})")

    # Import provider-specific client
    if provider.name == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=provider.base_url) if api_key else OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=tools,
            timeout=provider.timeout,
        )
        return response.model_dump()

    elif provider.name == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        # Convert messages format for Anthropic
        system_msg = ""
        anthropic_messages = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            else:
                anthropic_messages.append(m)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=system_msg,
            messages=anthropic_messages,
        )
        # Convert to OpenAI-like format
        return {
            "choices": [{"message": {"content": response.content[0].text}}],
            "usage": {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
            },
        }

    elif provider.name == "local":
        import httpx
        payload = {"model": "llama3", "messages": messages, "stream": False}
        with httpx.Client(timeout=provider.timeout) as client:
            resp = client.post(f"{provider.base_url}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            return resp.json()

    raise ValueError(f"Unknown provider: {provider.name}")


def call_with_fallback(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    providers: list[ProviderConfig] | None = None,
) -> FallbackResult:
    """
    Try providers in order with circuit breaker and backoff.

    Returns FallbackResult with success=True on first successful call,
    or success=False if all providers fail.
    """
    if providers is None:
        providers = DEFAULT_PROVIDERS

    for provider in providers:
        breaker = get_breaker(provider.name)
        if not breaker.allow_request():
            logger.info("Skipping %s — circuit breaker OPEN", provider.name)
            continue

        last_error: Exception | None = None
        for attempt in range(provider.max_retries + 1):
            start = time.monotonic()
            try:
                response = _call_provider(provider, messages, tools)
                elapsed_ms = int((time.monotonic() - start) * 1000)
                breaker.record_success()
                logger.info("LLM call succeeded via %s in %dms", provider.name, elapsed_ms)
                return FallbackResult(
                    provider=provider.name,
                    response=response,
                    latency_ms=elapsed_ms,
                    success=True,
                )
            except Exception as exc:
                last_error = exc
                breaker.record_failure()
                if attempt < provider.max_retries:
                    delay = min(provider.base_delay * (2 ** attempt), provider.max_delay)
                    logger.warning(
                        "LLM call failed on %s (attempt %d/%d), retrying in %.1fs: %s",
                        provider.name, attempt + 1, provider.max_retries + 1, delay, exc,
                    )
                    time.sleep(delay)

        logger.warning("All retries exhausted for %s: %s", provider.name, last_error)

    return FallbackResult(
        provider="none",
        success=False,
        error="All LLM providers failed",
    )
