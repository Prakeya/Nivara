"""
Tests for GroqClient — rate limiter, JSON parser, API client.

Covers:
- Rate limiter: exact limit, over limit, refill (clock-injected)
- Daily limit: rotation and exhaustion
- JSON parser: valid, markdown-wrapped, single quotes, extra text, malformed
- GroqClient: mocked API success/failures, normalized response
- Batch feasibility check
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from backend.groq_client import (
    GroqClient,
    GroqRateLimiter,
    GroqRateLimitError,
    GroqDailyLimitError,
    GroqTimeoutError,
    GroqAPIError,
    parse_llm_json,
    check_batch_feasible,
    DEFAULT_MODEL,
    FALLBACK_MODEL,
)


# ---------------------------------------------------------------------------
# Test clock helper
# ---------------------------------------------------------------------------

class _FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


# ---------------------------------------------------------------------------
# Rate limiter tests
# ---------------------------------------------------------------------------

class TestGroqRateLimiter:
    def test_allows_under_limit(self) -> None:
        limiter = GroqRateLimiter(requests_per_min=3, tokens_per_min=1000)
        limiter.pre_flight(50)  # should not raise

    def test_allows_exactly_at_request_limit(self) -> None:
        clock = _FakeClock()
        limiter = GroqRateLimiter(requests_per_min=3, tokens_per_min=1000, now_fn=clock)
        limiter.pre_flight(50)
        limiter.pre_flight(50)
        limiter.pre_flight(50)  # exactly at limit — should pass

    def test_allows_exactly_at_token_limit(self) -> None:
        clock = _FakeClock()
        limiter = GroqRateLimiter(requests_per_min=10, tokens_per_min=1000, now_fn=clock)
        limiter.pre_flight(1000)  # exactly at token limit — should pass

    def test_request_over_limit_raises(self) -> None:
        clock = _FakeClock()
        limiter = GroqRateLimiter(requests_per_min=2, tokens_per_min=1000, now_fn=clock)
        limiter.pre_flight(50)
        limiter.pre_flight(50)
        with pytest.raises(GroqRateLimitError):
            limiter.pre_flight(50)  # 3rd request over 2/min limit

    def test_token_over_limit_raises(self) -> None:
        clock = _FakeClock()
        limiter = GroqRateLimiter(requests_per_min=100, tokens_per_min=1000, now_fn=clock)
        limiter.pre_flight(600)
        with pytest.raises(GroqRateLimitError):
            limiter.pre_flight(600)  # would exceed 1000 tokens/min

    def test_request_bucket_refills(self) -> None:
        clock = _FakeClock()
        limiter = GroqRateLimiter(requests_per_min=1, tokens_per_min=1000, now_fn=clock)
        limiter.pre_flight(50)
        with pytest.raises(GroqRateLimitError):
            limiter.pre_flight(50)
        # Advance ~60s → refilled
        clock.advance(61)
        limiter.pre_flight(50)  # should now pass

    def test_token_bucket_refills(self) -> None:
        clock = _FakeClock()
        limiter = GroqRateLimiter(requests_per_min=100, tokens_per_min=1000, now_fn=clock)
        limiter.pre_flight(900)
        with pytest.raises(GroqRateLimitError):
            limiter.pre_flight(900)
        clock.advance(61)
        limiter.pre_flight(900)  # should now pass

    def test_requests_available_tracks(self) -> None:
        clock = _FakeClock()
        limiter = GroqRateLimiter(requests_per_min=3, tokens_per_min=1000, now_fn=clock)
        assert limiter.requests_available() == 3
        limiter.pre_flight(50)
        assert limiter.requests_available() == 2
        clock.advance(20)  # ~1/3 refill
        assert limiter.requests_available() == 3 or limiter.requests_available() == 2

    def test_daily_limit_raises(self) -> None:
        clock = _FakeClock()
        limiter = GroqRateLimiter(
            requests_per_min=1000, tokens_per_min=100_000, tokens_per_day=500, now_fn=clock
        )
        limiter.pre_flight(300)
        limiter.pre_flight(200)
        with pytest.raises(GroqDailyLimitError):
            limiter.pre_flight(100)  # 500 + 100 > 500 daily limit

    def test_daily_limit_rotation(self) -> None:
        clock = _FakeClock()
        limiter = GroqRateLimiter(
            requests_per_min=1000, tokens_per_min=100_000, tokens_per_day=500, now_fn=clock
        )
        limiter.pre_flight(300)
        assert limiter.daily_usage() == 300
        # Simulate a new day by using _set_now with a clock pointing to a later date
        # (monotonic clock is used for buckets; daily rotation uses real wall clock)
        limiter._force_reset_day_for_test()
        assert limiter.daily_usage() == 0

    def test_daily_usage_tracks_post_call(self) -> None:
        limiter = GroqRateLimiter(requests_per_min=100, tokens_per_min=50_000)
        limiter.pre_flight(100)
        limiter.post_call(50, 30)
        assert limiter.daily_usage() == 100  # pre_flight accounted usage


# ---------------------------------------------------------------------------
# JSON parser tests
# ---------------------------------------------------------------------------

class TestParseLlmJson:
    def test_parses_valid_json(self) -> None:
        raw = '{"classification": "TIMING_MISMATCH", "explanation": "delayed", "confidence": 0.8, "cited_evidence": ["timing_evidence"]}'
        result = parse_llm_json(raw)
        assert result is not None
        assert result["classification"] == "TIMING_MISMATCH"
        assert result["confidence"] == 0.8
        assert result["cited_evidence"] == ["timing_evidence"]

    def test_strips_markdown_code_block(self) -> None:
        raw = 'Sure! Here is my analysis:\n\n```json\n{"classification": "REFUND_TIMING", "explanation": "refund caused it", "confidence": 0.9, "cited_evidence": ["refund_evidence"]}\n```\n\nHope that helps!'
        result = parse_llm_json(raw)
        assert result is not None
        assert result["classification"] == "REFUND_TIMING"
        assert result["explanation"] == "refund caused it"

    def test_handles_single_quotes(self) -> None:
        raw = "{'classification': 'UNEXPLAINED', 'explanation': 'no cause found', 'confidence': 0.4, 'cited_evidence': []}"
        result = parse_llm_json(raw)
        assert result is not None
        assert result["classification"] == "UNEXPLAINED"

    def test_handles_extra_text_with_json(self) -> None:
        raw = "Analysis: the discrepancy looks like a timing issue.\n\n{\"classification\": \"TIMING_MISMATCH\", \"explanation\": \"T+2 cycle\", \"confidence\": 0.7, \"cited_evidence\": [\"timing_evidence\", \"bank_credit_evidence\"]}\n\nDone."
        result = parse_llm_json(raw)
        assert result is not None
        assert result["classification"] == "TIMING_MISMATCH"
        assert len(result["cited_evidence"]) == 2

    def test_regex_fallback(self) -> None:
        raw = "classification=TIMING_MISMATCH explanation=delayed confidence=0.65 cited_evidence=[timing_evidence]"
        result = parse_llm_json(raw)
        assert result is not None
        assert result["classification"] == "TIMING_MISMATCH"
        assert result["confidence"] == 0.65

    def test_empty_text_returns_none(self) -> None:
        assert parse_llm_json("") is None

    def test_no_json_returns_none(self) -> None:
        assert parse_llm_json("This is just a sentence with no JSON.") is None

    def test_garbage_returns_none(self) -> None:
        assert parse_llm_json("!!![][][][not json") is None


# ---------------------------------------------------------------------------
# GroqClient tests (mocked SDK)
# ---------------------------------------------------------------------------

class TestGroqClient:
    def test_requires_api_key(self) -> None:
        with pytest.raises(ValueError):
            GroqClient(api_key="")

    def test_default_model(self) -> None:
        client = GroqClient(api_key="gsk_test_key")
        assert client.model == DEFAULT_MODEL

    def test_estimate_cost_zero(self) -> None:
        client = GroqClient(api_key="gsk_test_key")
        assert client.estimate_cost(1000, 500) == 0.0

    def test_estimate_tokens(self) -> None:
        client = GroqClient(api_key="gsk_test_key")
        messages = [{"role": "user", "content": "x" * 200}]
        est = client.estimate_tokens(messages)
        assert est >= 50

    def _mock_sdk(self, content: str, tokens_in: int = 120, tokens_out: int = 40, error: Exception | None = None):
        mock_response = MagicMock()
        mock_response.choices[0].message.content = content
        mock_response.usage.prompt_tokens = tokens_in
        mock_response.usage.completion_tokens = tokens_out
        mock_sdk = MagicMock()
        if error is not None:
            mock_sdk.chat.completions.create.side_effect = error
        else:
            mock_sdk.chat.completions.create.return_value = mock_response
        return mock_sdk

    def test_complete_success_normalized(self) -> None:
        mock_sdk = self._mock_sdk('{"classification": "UNEXPLAINED"}', 120, 40)
        client = GroqClient(api_key="gsk_test_key")
        client._client = mock_sdk

        result = client.complete([{"role": "user", "content": "hi"}])
        assert result["text"] == '{"classification": "UNEXPLAINED"}'
        assert result["tokens_in"] == 120
        assert result["tokens_out"] == 40
        assert result["model"] == DEFAULT_MODEL
        assert result["latency_ms"] >= 0

    def test_complete_timeout_raises(self) -> None:
        mock_sdk = self._mock_sdk("", error=TimeoutError("request timed out"))
        client = GroqClient(api_key="gsk_test_key")
        client._client = mock_sdk

        with pytest.raises(GroqTimeoutError):
            client.complete([{"role": "user", "content": "hi"}])

    def test_complete_rate_limit_raises(self) -> None:
        mock_sdk = self._mock_sdk("", error=Exception("429 rate limit exceeded"))
        client = GroqClient(api_key="gsk_test_key")
        client._client = mock_sdk

        with pytest.raises(GroqRateLimitError):
            client.complete([{"role": "user", "content": "hi"}])

    def test_complete_api_error_raises(self) -> None:
        mock_sdk = self._mock_sdk("", error=Exception("500 Internal Server Error"))
        client = GroqClient(api_key="gsk_test_key")
        client._client = mock_sdk

        with pytest.raises(GroqAPIError):
            client.complete([{"role": "user", "content": "hi"}])

    @patch("backend.groq_client.GroqClient._get_sdk_client")
    def test_complete_preflight_limit_blocks(self, mock_get_client) -> None:
        clock = _FakeClock()
        limiter = GroqRateLimiter(requests_per_min=1, tokens_per_min=1000, now_fn=clock)
        limiter.pre_flight(50)

        client = GroqClient(api_key="gsk_test_key", rate_limiter=limiter)
        with pytest.raises(GroqRateLimitError):
            client.complete([{"role": "user", "content": "hi"}])
        # No API call should have been made
        mock_get_client.assert_not_called()

    def test_fallback_model_constant(self) -> None:
        assert FALLBACK_MODEL == "llama-3.1-8b-instant"


# ---------------------------------------------------------------------------
# Batch feasibility
# ---------------------------------------------------------------------------

class TestCheckBatchFeasible:
    def test_batch_within_limit(self) -> None:
        limiter = GroqRateLimiter(tokens_per_day=1_000_000)
        check_batch_feasible(10, tokens_per_settlement=100, rate_limiter=limiter)

    def test_batch_exceeds_daily_limit(self) -> None:
        limiter = GroqRateLimiter(tokens_per_day=1000)
        with pytest.raises(ValueError, match="exceeding remaining daily limit"):
            check_batch_feasible(10, tokens_per_settlement=200, rate_limiter=limiter)