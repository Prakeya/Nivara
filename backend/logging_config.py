"""
Structured JSON logging with correlation IDs.

Each request gets a unique correlation ID that propagates through all log entries.
Logs are emitted as JSON for easy ingestion by ELK/Datadog/Grafana.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# Correlation ID propagated across async tasks
correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


class JSONFormatter(logging.Formatter):
    """Emit log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "correlation_id": correlation_id.get("-"),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Attach extra fields
        for key in ("settlement_id", "upload_hash", "job_id", "duration_ms", "status_code"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        return json.dumps(log_entry, default=str)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure structured JSON logging for the nivara logger."""
    logger = logging.getLogger("nivara")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers
    logger.handlers.clear()

    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def generate_correlation_id() -> str:
    """Generate a short correlation ID."""
    return uuid.uuid4().hex[:12]


class CorrelationMiddleware:
    """FastAPI middleware that assigns a correlation ID to every request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            cid = headers.get(b"x-correlation-id", b"").decode() or generate_correlation_id()
            correlation_id.set(cid)

            async def send_with_correlation(message):
                if message["type"] == "http.response.start":
                    response_headers = list(message.get("headers", []))
                    response_headers.append((b"x-request-id", cid.encode()))
                    message = {**message, "headers": response_headers}
                await send(message)

            return await self.app(scope, receive, send_with_correlation)
        return await self.app(scope, receive, send)
