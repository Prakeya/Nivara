"""
Groq AI client with rate limiting for free tier.

Groq free tier limits (approximate):
- 20 requests per minute
- 6000 tokens per minute
- 1,000,000 tokens per day

Design:
- GroqClient: OpenAI-compatible wrapper around Groq's /chat/completions API.
- GroqRateLimiter: thread-safe token-bucket rate limiter (requests + tokens).
- Robust JSON parser for Llama/Mistral output quirks (markdown wrap, single quotes).
- Groq is FREE tier → estimated_cost = 0.0 (cost tracking stays for future paid tier).

Rules:
- If rate limit hit → GroqRateLimitError (retry handled by caller).
- If daily token limit hit → GroqDailyLimitError → UNRESOLVED → human review.
- If JSON parse fails → return None (caller decides to UNRESOLVED, never guess).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger("nivara.groq")

# Free tier defaults (verify against current Groq docs)
DEFAULT_REQUESTS_PER_MIN = 20
DEFAULT_TOKENS_PER_MIN = 6000
DEFAULT_TOKENS_PER_DAY = 1_000_000

DEFAULT_MODEL = "llama-3.1-70b-versatile"
FALLBACK_MODEL = "llama-3.1-8b-instant"

API_BASE_URL = "https://api.groq.com/openai/v1"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class GroqError(Exception):
    """Base class for Groq failures."""


class GroqRateLimitError(GroqError):
    """Rate limit exceeded (requests/min or tokens/min)."""

    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__(f"Groq rate limit exceeded: {resource}")


class GroqDailyLimitError(GroqError):
    """Daily token limit exhausted — must go to UNRESOLVED→human review."""


class GroqTimeoutError(GroqError):
    """Groq request timed out."""


class GroqAPIError(GroqError):
    """Groq API returned an error."""


# ---------------------------------------------------------------------------
# Token bucket (thread-safe refill logic is handled by the rate limiter)
# ---------------------------------------------------------------------------

class _TokenBucket:
    """Simple token bucket. Not thread-safe by itself; use with the limiter's lock."""

    def __init__(
        self,
        capacity: float,
        refill_per_sec: float,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capacity = float(capacity)
        self.refill_per_sec = refill_per_sec
        self.tokens = float(capacity)
        self.last_refill = now_fn()
        self.now_fn = now_fn

    def _refill(self) -> None:
        now = self.now_fn()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
            self.last_refill = now

    def try_take(self, amount: float) -> bool:
        self._refill()
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False

    def consume(self, amount: float) -> None:
        self._refill()
        self.tokens = max(0.0, self.tokens - amount)


# ---------------------------------------------------------------------------
# Groq rate limiter
# ---------------------------------------------------------------------------

class GroqRateLimiter:
    """Thread-safe rate limiter for Groq free tier."""

    def __init__(
        self,
        requests_per_min: int = DEFAULT_REQUESTS_PER_MIN,
        tokens_per_min: int = DEFAULT_TOKENS_PER_MIN,
        tokens_per_day: int = DEFAULT_TOKENS_PER_DAY,
        now_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        self._requests_per_min = requests_per_min
        self._tokens_per_min = tokens_per_min
        self._tokens_per_day = tokens_per_day
        monotonic_fn = now_fn or time.monotonic
        self._req_bucket = _TokenBucket(requests_per_min, requests_per_min / 60.0, monotonic_fn)
        self._tok_bucket = _TokenBucket(tokens_per_min, tokens_per_min / 60.0, monotonic_fn)
        self._lock = threading.Lock()
        # Daily usage tracking (resets on UTC date change)
        self._day_usage = 0
        self._day_key = datetime.now(timezone.utc).date()
        self._real_now = now_fn

    # -- testing helpers -----------------------------------------------------

    def _force_consume_requests(self, n: int) -> None:
        """Testing helper: consume request tokens without refill considerations."""
        with self._lock:
            self._req_bucket.tokens -= n

    def _force_consume_tokens(self, n: int) -> None:
        """Testing helper: consume token tokens without refill considerations."""
        with self._lock:
            self._tok_bucket.tokens -= n

    def _set_now(self, now_fn: Callable[[], float]) -> None:
        """Testing helper: override the clock for both buckets."""
        with self._lock:
            self._req_bucket.now_fn = now_fn
            self._tok_bucket.now_fn = now_fn
            self._real_now = now_fn

    def _force_reset_day_for_test(self) -> None:
        """Testing helper: reset daily usage tracking."""
        with self._lock:
            self._day_key = datetime.now(timezone.utc).date()
            self._day_usage = 0

    def requests_available(self) -> int:
        with self._lock:
            self._req_bucket._refill()
            return int(self._req_bucket.tokens)

    def tokens_available(self) -> int:
        with self._lock:
            self._tok_bucket._refill()
            return int(self._tok_bucket.tokens)

    # -- public API ----------------------------------------------------------

    def pre_flight(self, n_tokens_estimate: int = 100, n_requests: int = 1) -> None:
        """
        Check whether a request is allowed under all limits.

        Args:
            n_tokens_estimate: Estimated tokens the call will consume.
            n_requests: Number of requests this represents (default 1).

        Raises:
            GroqRateLimitError: requests/min or tokens/min would be exceeded.
            GroqDailyLimitError: daily token limit would be exceeded.
        """
        with self._lock:
            self._rotate_day()
            # Request bucket
            if not self._req_bucket.try_take(float(n_requests)):
                raise GroqRateLimitError("requests_per_minute")
            # Token bucket (per-minute). Refund request on failure.
            if not self._tok_bucket.try_take(float(n_tokens_estimate)):
                self._req_bucket.consume(float(n_requests))
                raise GroqRateLimitError("tokens_per_minute")
            # Daily limit
            if self._day_usage + n_tokens_estimate > self._tokens_per_day:
                raise GroqDailyLimitError(
                    f"Daily token limit ({self._tokens_per_day}) would be exceeded"
                )
            self._day_usage += n_tokens_estimate

    def post_call(self, tokens_in: int, tokens_out: int) -> None:
        """
        Record actual token usage after a successful call.

        Adjusts the per-minute token bucket and daily usage to the real counts.
        """
        with self._lock:
            self._rotate_day()
            actual = tokens_in + tokens_out
            self._tok_bucket.consume(float(max(0, actual)))
            self._day_usage = max(0, self._day_usage)

    def _rotate_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._day_key:
            self._day_key = today
            self._day_usage = 0

    def daily_usage(self) -> int:
        with self._lock:
            self._rotate_day()
            return self._day_usage


# ---------------------------------------------------------------------------
# JSON response parser (robust for Llama/Mistral output quirks)
# ---------------------------------------------------------------------------

def _extract_code_block(text: str) -> str:
    """Strip markdown code fences (```json ... ``` or ``` ... ```)."""
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    return text


def _extract_braced_object(text: str) -> Optional[str]:
    """Extract the first balanced { ... } block from text."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _normalize_single_quotes(text: str) -> str:
    """Best-effort conversion of Python-style single-quoted strings to JSON double quotes."""
    if '"' in text:
        return text
    result = re.sub(r"'", '"', text)
    result = result.replace('"True"', "true").replace('"False"', "false")
    result = result.replace('"None"', "null")
    return result


def parse_llm_json(text: str) -> Optional[dict[str, Any]]:
    """
    Parse an LLM response into a JSON dict.

    Handles:
    - Markdown code block wrapping (```json ... ```)
    - Explanatory text before/after the JSON block
    - Single quotes (Python-style literals)
    - Malformed input (returns None — caller decides, never guesses)

    Returns:
        Parsed dict, or None if no valid JSON object could be extracted.
    """
    if not text:
        return None

    # 1. Strip code fences
    cleaned = _extract_code_block(text)

    # 2. Try direct parse
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # 3. Extract balanced braced object and try parse
    block = _extract_braced_object(cleaned)
    if block:
        try:
            data = json.loads(block)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        # 4. Single-quote normalization
        normalized = _normalize_single_quotes(block)
        try:
            data = json.loads(normalized)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # 5. Regex fallback: pull key fields
    return _regex_extract(cleaned)


def _regex_extract(text: str) -> Optional[dict[str, Any]]:
    """Last-resort extraction of known keys via regex. Returns None if insufficient."""
    classification = re.search(
        r"['\"]?classification['\"]?\s*[:=]\s*['\"]?([^'\"\s,}\]]+)",
        text,
    )
    explanation = re.search(
        r"['\"]?explanation['\"]?\s*[:=]\s*['\"]?([^'\"\s,}\]]+)",
        text,
    )
    confidence = re.search(
        r"['\"]?confidence['\"]?\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", text
    )
    if classification and explanation and confidence:
        cited = []
        cited_match = re.search(
            r"['\"]?cited_evidence['\"]?\s*[:=]\s*\[([^\]]*)\]", text
        )
        if cited_match:
            cited = [c.strip(" \"'") for c in cited_match.group(1).split(",") if c.strip()]
        return {
            "classification": classification.group(1),
            "explanation": explanation.group(1),
            "confidence": float(confidence.group(1)),
            "cited_evidence": cited,
        }
    return None


# ---------------------------------------------------------------------------
# Groq client
# ---------------------------------------------------------------------------

class GroqClient:
    """OpenAI-compatible client for Groq's /chat/completions API."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        rate_limiter: Optional[GroqRateLimiter] = None,
    ) -> None:
        if not api_key:
            raise ValueError("GroqClient requires a non-empty api_key")
        self._api_key = api_key
        self._model = model
        self._rate_limiter = rate_limiter or GroqRateLimiter()
        self._client = None

    @property
    def model(self) -> str:
        return self._model

    def _get_sdk_client(self):
        if self._client is None:
            try:
                from groq import Groq
            except ImportError as exc:
                raise GroqError(
                    "groq package not installed. Run: pip install groq"
                ) from exc
            self._client = Groq(api_key=self._api_key, base_url=API_BASE_URL)
        return self._client

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Rough token estimate for pre-flight rate limiting (chars / 4)."""
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        return max(50, int(total_chars / 4))

    def estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        """Groq free tier cost is 0.0. Kept for future paid-tier support."""
        return 0.0

    def complete(self, messages: list[dict[str, Any]], timeout: int = 15) -> dict[str, Any]:
        """
        Send messages to Groq and return a normalized dict.

        Returns:
            {
                "text": str,              # raw completion text (not yet parsed to JSON)
                "tokens_in": int,
                "tokens_out": int,
                "model": str,
                "latency_ms": int,
            }

        Raises:
            GroqRateLimitError, GroqDailyLimitError, GroqTimeoutError, GroqAPIError.
        """
        est_tokens = self.estimate_tokens(messages)

        # Pre-flight: raise if any limit would be exceeded
        self._rate_limiter.pre_flight(n_tokens_estimate=est_tokens, n_requests=1)

        client = self._get_sdk_client()
        start = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=self._model,
                messages=messages,
                timeout=timeout,
                temperature=0.0,
                max_tokens=1024,
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "timeout" in msg or "timed out" in msg:
                raise GroqTimeoutError(str(exc)) from exc
            if "rate" in msg or "429" in msg:
                raise GroqRateLimitError("api_429") from exc
            raise GroqAPIError(str(exc)) from exc

        latency_ms = int((time.monotonic() - start) * 1000)

        text = response.choices[0].message.content or ""
        usage = response.usage
        tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        tokens_out = getattr(usage, "completion_tokens", 0) or 0

        # Post-call: deduct actual tokens used
        self._rate_limiter.post_call(tokens_in, tokens_out)

        return {
            "text": text,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "model": self._model,
            "latency_ms": latency_ms,
        }


# ---------------------------------------------------------------------------
# Batch feasibility check (WATCH-G1): reject before processing if limits are hit
# ---------------------------------------------------------------------------

def check_batch_feasible(
    n_settlements: int,
    tokens_per_settlement: int = 300,
    rate_limiter: Optional[GroqRateLimiter] = None,
    tokens_per_day: int = DEFAULT_TOKENS_PER_DAY,
) -> None:
    """
    Validate that a batch can be processed under the Groq free tier.

    Raises:
        ValueError: if the batch would exceed daily token limits.
    """
    limiter = rate_limiter or GroqRateLimiter(tokens_per_day=tokens_per_day)
    daily_limit = limiter._tokens_per_day
    total_estimate = n_settlements * tokens_per_settlement
    remaining = daily_limit - limiter.daily_usage()
    if total_estimate > remaining:
        raise ValueError(
            f"Batch of {n_settlements} settlements would use ~{total_estimate} tokens, "
            f"exceeding remaining daily limit of {max(0, remaining)} tokens. "
            "Reduce batch size or retry tomorrow."
        )