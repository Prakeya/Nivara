"""
Prometheus metrics for Nivara.

Exposes /metrics endpoint with settlement throughput, latency, error rate.
Uses prometheus_client library (lightweight, no external deps beyond the lib).

Also maintains dependency-free in-memory trackers for LLM calls and Groq
free-tier quota consumption (used by GET /api/metrics), which work even when
prometheus_client is not installed.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

try:
    from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST  # type: ignore[import-not-found,unused-ignore]
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


if PROMETHEUS_AVAILABLE:
    PROMETHEUS_REGISTRY: Any | None = None
    try:
        from prometheus_client import CollectorRegistry

        PROMETHEUS_REGISTRY = CollectorRegistry()
    except Exception:
        pass

    if PROMETHEUS_REGISTRY is None:
        # Settlement processing metrics
        SETTLEMENTS_PROCESSED = Counter(
            "nivara_settlements_processed_total",
            "Total settlements processed",
            ["decision_state"],
        )
        SETTLEMENT_LATENCY = Histogram(
            "nivara_settlement_latency_seconds",
            "Time to process a single settlement",
            buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
        )
        BATCHES_PROCESSED = Counter(
            "nivara_batches_processed_total",
            "Total batches (uploads) processed",
        )
        UPLOAD_ERRORS = Counter(
            "nivara_upload_errors_total",
            "Total upload errors",
            ["error_type"],
        )
        ACTIVE_JOBS = Gauge(
            "nivara_active_jobs",
            "Number of currently processing jobs",
        )
        LLM_CALLS = Counter(
            "nivara_llm_calls_total",
            "Total LLM API calls",
            ["provider", "status"],
        )
        LLM_LATENCY = Histogram(
            "nivara_llm_latency_seconds",
            "LLM API call latency",
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
        )
        HUMAN_REVIEWS = Counter(
            "nivara_human_reviews_total",
            "Total human review decisions",
            ["decision"],
        )
        GROQ_DAILY_TOKENS_USED = Gauge(
            "nivara_groq_daily_tokens_used",
            "Groq daily tokens consumed",
            ["model"],
        )
    else:
        # Settlement processing metrics
        SETTLEMENTS_PROCESSED = Counter(
            "nivara_settlements_processed_total",
            "Total settlements processed",
            ["decision_state"],
            registry=PROMETHEUS_REGISTRY,
        )
        SETTLEMENT_LATENCY = Histogram(
            "nivara_settlement_latency_seconds",
            "Time to process a single settlement",
            buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
            registry=PROMETHEUS_REGISTRY,
        )
        BATCHES_PROCESSED = Counter(
            "nivara_batches_processed_total",
            "Total batches (uploads) processed",
            registry=PROMETHEUS_REGISTRY,
        )
        UPLOAD_ERRORS = Counter(
            "nivara_upload_errors_total",
            "Total upload errors",
            ["error_type"],
            registry=PROMETHEUS_REGISTRY,
        )
        ACTIVE_JOBS = Gauge(
            "nivara_active_jobs",
            "Number of currently processing jobs",
            registry=PROMETHEUS_REGISTRY,
        )
        LLM_CALLS = Counter(
            "nivara_llm_calls_total",
            "Total LLM API calls",
            ["provider", "status"],
            registry=PROMETHEUS_REGISTRY,
        )
        LLM_LATENCY = Histogram(
            "nivara_llm_latency_seconds",
            "LLM API call latency",
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
            registry=PROMETHEUS_REGISTRY,
        )
        HUMAN_REVIEWS = Counter(
            "nivara_human_reviews_total",
            "Total human review decisions",
            ["decision"],
            registry=PROMETHEUS_REGISTRY,
        )
        GROQ_DAILY_TOKENS_USED = Gauge(
            "nivara_groq_daily_tokens_used",
            "Groq daily tokens consumed",
            ["model"],
            registry=PROMETHEUS_REGISTRY,
        )

    def record_settlement(decision_state: str, latency_seconds: float) -> None:
        SETTLEMENTS_PROCESSED.labels(decision_state=decision_state).inc()
        SETTLEMENT_LATENCY.observe(latency_seconds)

    def record_batch() -> None:
        BATCHES_PROCESSED.inc()

    def record_upload_error(error_type: str) -> None:
        UPLOAD_ERRORS.labels(error_type=error_type).inc()

    def record_llm_call(provider: str, status: str, latency_seconds: float) -> None:
        LLM_CALLS.labels(provider=provider, status=status).inc()
        LLM_LATENCY.observe(latency_seconds)

    def record_human_review(decision: str) -> None:
        HUMAN_REVIEWS.labels(decision=decision).inc()

    def set_active_jobs(count: int) -> None:
        ACTIVE_JOBS.set(count)

    def get_metrics() -> bytes:
        if PROMETHEUS_REGISTRY is not None:
            result = generate_latest(PROMETHEUS_REGISTRY)
        else:
            result = generate_latest()
        if not isinstance(result, bytes):
            raise TypeError("Prometheus metrics output must be bytes")
        return result

    def get_content_type() -> str:
        if not isinstance(CONTENT_TYPE_LATEST, str):
            raise TypeError("Prometheus content type must be a string")
        return CONTENT_TYPE_LATEST
else:
    # Stub functions when prometheus_client is not installed
    def record_settlement(decision_state: str, latency_seconds: float) -> None: pass
    def record_batch() -> None: pass
    def record_upload_error(error_type: str) -> None: pass
    def record_llm_call(provider: str, status: str, latency_seconds: float) -> None: pass
    def record_human_review(decision: str) -> None: pass
    def set_active_jobs(count: int) -> None: pass
    def get_metrics() -> bytes: return b"# prometheus_client not installed\n"
    def get_content_type() -> str: return "text/plain"


# ---------------------------------------------------------------------------
# Dependency-free in-memory trackers (GET /api/metrics)
#
# These always work — even without prometheus_client — and back the
# Metrics Dashboard (pie chart, Groq quota progress bar, latency, errors).
# ---------------------------------------------------------------------------

_llm_lock = threading.Lock()
_llm_calls = 0
_llm_errors = 0
_llm_latency_ms = 0.0

_groq_lock = threading.Lock()
_groq_daily_used: dict[str, int] = {}


def reset_in_memory_metrics() -> None:
    """Reset dashboard trackers so tests can start from a deterministic state."""
    global _llm_calls, _llm_errors, _llm_latency_ms
    with _llm_lock:
        _llm_calls = 0
        _llm_errors = 0
        _llm_latency_ms = 0.0
    with _groq_lock:
        _groq_daily_used.clear()


def record_llm_call_metric(status: str, latency_ms: float) -> None:
    """Record an LLM call outcome for the in-memory dashboard metrics."""
    global _llm_calls, _llm_errors, _llm_latency_ms
    with _llm_lock:
        _llm_calls += 1
        _llm_latency_ms += max(0.0, float(latency_ms))
        if status != "ok":
            _llm_errors += 1
    if PROMETHEUS_AVAILABLE:
        try:
            record_llm_call("groq", status, latency_ms / 1000.0)
        except Exception:
            pass


def record_groq_usage(tokens: int, model: str) -> None:
    """Accumulate estimated token consumption against the Groq free tier."""
    with _groq_lock:
        _groq_daily_used[model] = _groq_daily_used.get(model, 0) + max(0, int(tokens))
    if PROMETHEUS_AVAILABLE:
        try:
            with _groq_lock:
                GROQ_DAILY_TOKENS_USED.labels(model=model).set(_groq_daily_used[model])
        except Exception:
            pass


def llm_metrics_snapshot() -> dict[str, Any]:
    """Return aggregate LLM call metrics for the dashboard."""
    with _llm_lock:
        return {
            "total_calls": _llm_calls,
            "errors": _llm_errors,
            "avg_latency_ms": round(_llm_latency_ms / _llm_calls, 1) if _llm_calls else 0.0,
            "error_rate": round(_llm_errors / _llm_calls, 4) if _llm_calls else 0.0,
        }


def groq_daily_usage_snapshot() -> dict[str, Any]:
    """Return Groq free-tier quota usage for the dashboard progress bar."""
    from backend.groq_client import DEFAULT_TOKENS_PER_DAY

    with _groq_lock:
        used = sum(_groq_daily_used.values())
        by_model = dict(_groq_daily_used)
    limit = int(DEFAULT_TOKENS_PER_DAY)
    remaining = max(0, limit - used)
    return {
        "daily_limit": limit,
        "used_tokens": used,
        "remaining_tokens": remaining,
        "pct_used": round((used / limit) * 100, 2) if limit else 0.0,
        "by_model": by_model,
    }
