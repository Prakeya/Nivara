"""In-memory job store and rate limiter.

Extracted from backend/main.py (Task 7 router split). This module is
dependency-free with respect to the rest of the app: it has no imports from
backend.routes and no imports from any other backend module, so it can be
imported safely from any route module without risk of circular imports.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------

MAX_JOBS = 10000


@dataclass
class JobResult:
    job_id: str
    status: str  # "processing" | "completed" | "error"
    upload_hash: str = ""
    created_at: str = ""
    error: str = ""
    # Populated after processing
    total_settlements: int = 0
    clean_matches: int = 0
    exceptions: int = 0
    unresolved: int = 0
    math_discrepancies: int = 0
    ai_investigations: int = 0
    match_rate: float = 0.0
    csv_counts: dict[str, Any] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)
    batch_analysis: list[dict[str, Any]] = field(default_factory=list)
    audit_records: list[dict[str, Any]] = field(default_factory=list)


_jobs: dict[str, JobResult] = {}
_jobs_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Human review store (keyed by settlement_id)
# ---------------------------------------------------------------------------

# Typed as Any to avoid importing backend.models here (kept dependency-free);
# routes/review.py stores HumanReviewDecision instances in this dict.
_human_reviews: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Rate limiting (simple in-memory sliding window)
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Per-IP sliding window rate limiter."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._upload_limit = 100  # req per 60s
        self._api_limit = 300  # req per 60s
        self._window = 60.0

    def _check(self, key: str, limit: int) -> bool:
        now = time.time()
        cutoff = now - self._window
        self._hits[key] = [t for t in self._hits[key] if t > cutoff]
        if len(self._hits[key]) >= limit:
            return False
        self._hits[key].append(now)
        return True

    def check_upload(self, ip: str) -> bool:
        return self._check(f"upload:{ip}", self._upload_limit)

    def check_api(self, ip: str) -> bool:
        return self._check(f"api:{ip}", self._api_limit)


_rate_limiter = _RateLimiter()
