"""
Tests for Fallback Chain — provider ordering, circuit breaker, backoff.

Covers:
- Provider ordering: OpenAI → Anthropic → Local
- Circuit breaker integration
- Success on first provider
- Fallback to second provider
- All providers fail → None
"""

from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pytest

from backend.fallback_chain import (
    call_with_fallback,
    FallbackResult,
    ProviderConfig,
    _call_provider,
)
from backend.circuit_breaker import CircuitBreaker, CircuitState, get_breaker


def _reset_breakers() -> None:
    """Reset all circuit breakers for clean test state."""
    from backend import circuit_breaker
    circuit_breaker._breakers.clear()


class TestProviderConfig:
    def test_default_providers_order(self) -> None:
        from backend.fallback_chain import DEFAULT_PROVIDERS
        names = [p.name for p in DEFAULT_PROVIDERS]
        assert names == ["openai", "anthropic", "local"]


class TestCallProvider:
    @patch("backend.fallback_chain.os.environ.get")
    def test_missing_api_key_raises(self, mock_env: MagicMock) -> None:
        mock_env.return_value = ""
        provider = ProviderConfig(name="openai", api_key_env="OPENAI_API_KEY")
        with pytest.raises(ValueError, match="No API key"):
            _call_provider(provider, [{"role": "user", "content": "hi"}])


class TestCallWithFallback:
    def setup_method(self) -> None:
        _reset_breakers()

    @patch("backend.fallback_chain._call_provider")
    def test_first_provider_succeeds(self, mock_call: MagicMock) -> None:
        mock_call.return_value = {"choices": [{"message": {"content": "ok"}}]}
        providers = [ProviderConfig(name="openai", api_key_env="")]
        result = call_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            providers=providers,
        )
        assert result.success is True
        assert result.provider == "openai"
        assert mock_call.call_count == 1

    @patch("backend.fallback_chain._call_provider")
    def test_fallback_to_second_provider(self, mock_call: MagicMock) -> None:
        call_count = 0

        def side_effect(provider, messages, tools=None):
            nonlocal call_count
            call_count += 1
            if provider.name == "openai":
                raise RuntimeError("OpenAI down")
            return {"choices": [{"message": {"content": "ok"}}]}

        mock_call.side_effect = side_effect
        providers = [
            ProviderConfig(name="openai", api_key_env="", max_retries=0),
            ProviderConfig(name="anthropic", api_key_env="ANTHROPIC_API_KEY", max_retries=0),
        ]
        result = call_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            providers=providers,
        )
        assert result.success is True
        assert result.provider == "anthropic"

    @patch("backend.fallback_chain._call_provider")
    def test_all_providers_fail(self, mock_call: MagicMock) -> None:
        mock_call.side_effect = RuntimeError("all down")
        providers = [
            ProviderConfig(name="openai", api_key_env="", max_retries=0),
            ProviderConfig(name="anthropic", api_key_env="ANTHROPIC_API_KEY", max_retries=0),
        ]
        result = call_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            providers=providers,
        )
        assert result.success is False
        assert result.provider == "none"
        assert result.error is not None

    @patch("backend.fallback_chain._call_provider")
    def test_circuit_breaker_skips_open_provider(self, mock_call: MagicMock) -> None:
        breaker = get_breaker("openai")
        # Force breaker open
        for _ in range(10):
            breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        providers = [
            ProviderConfig(name="openai", api_key_env="", max_retries=0),
            ProviderConfig(name="anthropic", api_key_env="ANTHROPIC_API_KEY", max_retries=0),
        ]

        def side_effect(provider, messages, tools=None):
            return {"choices": [{"message": {"content": "ok"}}]}

        mock_call.side_effect = side_effect
        result = call_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            providers=providers,
        )
        assert result.success is True
        assert result.provider == "anthropic"
        # openai was skipped, not called
        assert mock_call.call_count == 1

    @patch("backend.fallback_chain._call_provider")
    def test_backoff_timing(self, mock_call: MagicMock) -> None:
        mock_call.side_effect = RuntimeError("fail")
        providers = [
            ProviderConfig(name="openai", api_key_env="", max_retries=2, base_delay=0.01, max_delay=0.1),
        ]
        start = time.monotonic()
        result = call_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            providers=providers,
        )
        elapsed = time.monotonic() - start
        assert result.success is False
        # 3 attempts: 0s, 0.01s, 0.02s = ~0.03s total
        assert elapsed >= 0.02

    @patch("backend.fallback_chain._call_provider")
    def test_latency_recorded(self, mock_call: MagicMock) -> None:
        mock_call.return_value = {"choices": [{"message": {"content": "ok"}}]}
        providers = [ProviderConfig(name="openai", api_key_env="", max_retries=0)]
        result = call_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            providers=providers,
        )
        assert result.latency_ms >= 0
