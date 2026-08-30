"""
Prometheus metrics for Nivara.

Exposes /metrics endpoint with settlement throughput, latency, error rate.
Uses prometheus_client library (lightweight, no external deps beyond the lib).
"""

from __future__ import annotations

import time
from typing import Optional

try:
    from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


if PROMETHEUS_AVAILABLE:
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
        return generate_latest()

    def get_content_type() -> str:
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
