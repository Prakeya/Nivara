"""
LLM retry logic with exponential backoff and circuit breaker.

Circuit breaker states:
- CLOSED: Normal operation, requests pass through
- OPEN: Too many failures, reject requests immediately
- HALF_OPEN: Testing if service recovered
"""

from __future__ import annotations

import time
import logging
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger("nivara")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-provider circuit breaker with exponential backoff."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max: int = 3,
    ):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max = half_open_max
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float = 0.0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time > self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
        return self._state

    def record_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self._half_open_max:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                logger.info("Circuit breaker closed — service recovered")
        elif self._state == CircuitState.CLOSED:
            self._failure_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.warning("Circuit breaker opened — half-open test failed")
        elif self._failure_count >= self._failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning("Circuit breaker opened — %d failures", self._failure_count)

    def allow_request(self) -> bool:
        state = self.state
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.HALF_OPEN:
            return True
        return False


# Per-provider circuit breakers
_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(provider: str) -> CircuitBreaker:
    if provider not in _breakers:
        _breakers[provider] = CircuitBreaker()
    return _breakers[provider]


def retry_with_backoff(
    func: Callable[..., Any],
    *args: Any,
    provider: str = "openai",
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    **kwargs: Any,
) -> Any:
    """Execute func with exponential backoff and circuit breaker."""
    breaker = get_breaker(provider)

    if not breaker.allow_request():
        raise RuntimeError(f"Circuit breaker OPEN for {provider} — request rejected")

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            result = func(*args, **kwargs)
            breaker.record_success()
            return result
        except Exception as e:
            last_error = e
            breaker.record_failure()
            if attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), max_delay)
                logger.warning(
                    "LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, max_retries + 1, delay, e,
                )
                time.sleep(delay)

    assert last_error is not None
    raise last_error
