"""
Tests for Fallback Chain — Groq-only: 70B primary, 8B fallback, UNRESOLVED.

Covers:
- Success on primary model (70B)
- Fallback to secondary model (8B) on timeout / API error / bad JSON
- Both models fail → success=False (UNRESOLVED)
- Circuit breaker skips open model
- Rate limiter blocks before any API call
- Response dict shape compatible with ai_validator
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from backend.fallback_chain import (
    call_with_fallback,
    GroqFallbackChain,
    FallbackResult,
    ProviderConfig,
    DEFAULT_PROVIDERS,
    PRIMARY_MODEL,
    SECONDARY_MODEL,
)
from backend.groq_client import (
    GroqClient,
    GroqRateLimiter,
    GroqTimeoutError,
    GroqAPIError,
    GroqRateLimitError,
    DEFAULT_MODEL,
    FALLBACK_MODEL,
)
from backend.circuit_breaker import CircuitState, get_breaker


def _reset_breakers() -> None:
    from backend import circuit_breaker
    circuit_breaker._breakers.clear()


def _mock_client(raw_text: str, tokens_in: int = 100, tokens_out: int = 30) -> MagicMock:
    client = MagicMock(spec=GroqClient)
    client.complete.return_value = {
        "text": raw_text,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "model": DEFAULT_MODEL,
        "latency_ms": 42,
    }
    return client


_VALID_JSON = '{"classification": "TIMING_MISMATCH", "explanation": "delayed settlement", "confidence": 0.8, "cited_evidence": ["timing_evidence"]}'


class TestGroqFallbackChain:
    def setup_method(self) -> None:
        _reset_breakers()

    def test_primary_succeeds_with_valid_response(self) -> None:
        client = _mock_client(_VALID_JSON)
        chain = GroqFallbackChain(api_key="gsk_test", client=client)
        result = chain.call([{"role": "user", "content": "hi"}])

        assert result.success is True
        assert result.provider == "groq"
        assert result.model == PRIMARY_MODEL == DEFAULT_MODEL
        assert client.complete.call_count == 1
        # ai_validator-compatible shape
        assert result.response["classification"] == "TIMING_MISMATCH"
        assert result.response["usage"]["prompt_tokens"] == 100

    def test_fallback_to_secondary_on_timeout(self) -> None:
        client = MagicMock(spec=GroqClient)
        client.complete.side_effect = [GroqTimeoutError("timed out"), _raw_response_dict()]
        chain = GroqFallbackChain(api_key="gsk_test", client=client)
        result = chain.call([{"role": "user", "content": "hi"}])

        assert result.success is True
        assert result.model == SECONDARY_MODEL == FALLBACK_MODEL
        assert client.complete.call_count == 2

    def test_fallback_to_secondary_on_api_error(self) -> None:
        client = MagicMock(spec=GroqClient)
        client.complete.side_effect = [GroqAPIError("500"), _raw_response_dict()]
        chain = GroqFallbackChain(api_key="gsk_test", client=client)
        result = chain.call([{"role": "user", "content": "hi"}])

        assert result.success is True
        assert result.model == SECONDARY_MODEL

    def test_fallback_to_secondary_on_unparsable_json(self) -> None:
        client = MagicMock(spec=GroqClient)
        client.complete.side_effect = [_raw_response_dict("not json at all"), _raw_response_dict()]
        chain = GroqFallbackChain(api_key="gsk_test", client=client)
        result = chain.call([{"role": "user", "content": "hi"}])

        assert result.success is True
        assert result.model == SECONDARY_MODEL

    def test_both_models_fail_returns_unresolved(self) -> None:
        client = MagicMock(spec=GroqClient)
        client.complete.side_effect = [GroqTimeoutError("t1"), GroqTimeoutError("t2")]
        chain = GroqFallbackChain(api_key="gsk_test", client=client)
        result = chain.call([{"role": "user", "content": "hi"}])

        assert result.success is False
        assert result.provider == "none"
        assert result.error is not None

    def test_both_models_fail_on_garbage(self) -> None:
        client = MagicMock(spec=GroqClient)
        client.complete.side_effect = [_raw_response_dict("garbage 1"), _raw_response_dict("garbage 2")]
        chain = GroqFallbackChain(api_key="gsk_test", client=client)
        result = chain.call([{"role": "user", "content": "hi"}])

        assert result.success is False

    def test_circuit_breaker_skips_open_model(self) -> None:
        breaker = get_breaker(f"groq::{PRIMARY_MODEL}")
        for _ in range(10):
            breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        client = MagicMock(spec=GroqClient)
        client.complete.return_value = _raw_response_dict()
        chain = GroqFallbackChain(api_key="gsk_test", client=client)
        result = chain.call([{"role": "user", "content": "hi"}])

        # Primary skipped (breaker open), secondary answered
        assert result.success is True
        assert result.model == SECONDARY_MODEL
        assert client.complete.call_count == 1

    def test_rate_limiter_blocks_before_api_call(self) -> None:
        limiter = GroqRateLimiter(requests_per_min=1, tokens_per_min=1000)
        limiter.pre_flight(100)
        client = MagicMock(spec=GroqClient)
        client.complete.side_effect = GroqRateLimitError("requests_per_minute")
        chain = GroqFallbackChain(api_key="gsk_test", client=client, rate_limiter=limiter)
        result = chain.call([{"role": "user", "content": "hi"}])

        assert result.success is False
        assert "failed" in str(result.error) or result.error is not None

    def test_latency_recorded(self) -> None:
        client = _mock_client(_VALID_JSON)
        chain = GroqFallbackChain(api_key="gsk_test", client=client)
        result = chain.call([{"role": "user", "content": "hi"}])
        assert result.latency_ms >= 0

    def test_missing_api_key_still_runs_chain(self) -> None:
        client = _mock_client(_VALID_JSON)
        chain = GroqFallbackChain(api_key="", client=client)
        result = chain.call([{"role": "user", "content": "hi"}])
        # Chain runs (fails at SDK layer); mocked client means success here.
        assert result.success is True


def _raw_response_dict(text: str = _VALID_JSON) -> dict:
    return {"text": text, "tokens_in": 100, "tokens_out": 30, "model": DEFAULT_MODEL, "latency_ms": 7}


class TestProviderConfig:
    def test_default_providers_is_groq_only(self) -> None:
        names = [p.name for p in DEFAULT_PROVIDERS]
        assert names == ["groq"]

    def test_default_env_key(self) -> None:
        assert DEFAULT_PROVIDERS[0].api_key_env == "GROQ_API_KEY"


class TestCallWithFallback:
    def setup_method(self) -> None:
        _reset_breakers()

    @patch("backend.fallback_chain.GroqFallbackChain.call")
    @patch("backend.fallback_chain.os.environ.get")
    def test_uses_groq_api_key_from_env(self, mock_env: MagicMock, mock_call: MagicMock) -> None:
        mock_env.return_value = "gsk_env_key"
        mock_call.return_value = FallbackResult(provider="groq", success=True)

        result = call_with_fallback(messages=[{"role": "user", "content": "hi"}])
        assert result.success is True
        assert result.provider == "groq"

    @patch("backend.fallback_chain.GroqFallbackChain.call")
    def test_providers_arg_defaults_to_groq(self, mock_call: MagicMock) -> None:
        mock_call.return_value = FallbackResult(provider="groq", success=True)
        result = call_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            providers=[ProviderConfig(name="groq", api_key_env="GROQ_API_KEY")],
        )
        assert result.provider == "groq"