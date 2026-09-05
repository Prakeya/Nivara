"""
Phase 10: FastAPI Endpoints

App instantiation, middleware, startup checks, and router mounting only.
Route handler bodies live under backend/routes/ (Task 7 router split):

- routes/upload.py    POST /upload
- routes/status.py    GET /status/{job_id}
- routes/audit.py     GET /audit/{upload_hash}, GET /audit/{upload_hash}/verify,
                       GET /settlement/{settlement_id}
- routes/review.py    POST /api/review/{settlement_id}/decision,
                       GET /api/review/pending, GET /api/review/{settlement_id}
- routes/razorpay.py  POST /api/fetch-razorpay, POST /api/reconcile-razorpay
- routes/metrics.py   GET /api/metrics, GET /metrics
- routes/health.py    GET /health
- routes/v1.py         /v1/* versioned + paginated endpoints
- routes/frontend.py  static asset mounts, "/", and the SPA catch-all fallback

Shared in-memory job store + rate limiter: backend/job_store.py
Shared request-scoped helpers (audit logger, client IP, LLM config): backend/api_helpers.py
Shared result -> response shaping (incl. the /upload vs /api/reconcile-razorpay
response-shape fix): backend/response_shaping.py
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from backend.api_helpers import _validate_llm_config
from backend.logging_config import CorrelationMiddleware
from backend.routes import audit, frontend, health, metrics, razorpay, review, status, upload, v1

logger = logging.getLogger("nivara.api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    """Fail fast at boot if GROQ_API_KEY is missing (WATCH-G4)."""
    try:
        _validate_llm_config()
    except RuntimeError as exc:
        logger.error("Startup LLM validation failed: %s", exc)
        raise
    logger.info("Groq configuration validated")
    yield


app = FastAPI(
    title="Nivara",
    description="AI Settlement Intelligence Agent",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response  # type: ignore[no-any-return]


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CorrelationMiddleware)


# ---------------------------------------------------------------------------
# Router mounting
# ---------------------------------------------------------------------------

frontend.mount_static(app)

app.include_router(upload.router)
app.include_router(status.router)
app.include_router(audit.router)
app.include_router(review.router)
app.include_router(razorpay.router)
app.include_router(metrics.router)
app.include_router(health.router)
app.include_router(v1.router)

# frontend.router holds the "/{full_path:path}" SPA catch-all — must be
# mounted last, or it will shadow every route registered after it.
app.include_router(frontend.router)
