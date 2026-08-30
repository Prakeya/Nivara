"""
Deep health checks: DB connectivity, LLM connectivity, disk space.
"""

from __future__ import annotations

import os
import shutil
import time
from typing import Any


def check_database() -> dict[str, Any]:
    """Check database connectivity."""
    try:
        from backend.database import get_connection, is_postgres
        start = time.time()
        with get_connection() as conn:
            cursor = conn.execute("SELECT 1")
            cursor.fetchone()
        latency_ms = round((time.time() - start) * 1000, 2)
        return {"status": "ok", "backend": "postgres" if is_postgres() else "sqlite", "latency_ms": latency_ms}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def check_llm() -> dict[str, Any]:
    """Check LLM provider connectivity (if configured)."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"status": "not_configured", "provider": "none"}
    try:
        from openai import OpenAI
        start = time.time()
        client = OpenAI(api_key=api_key)
        # Lightweight models list call
        client.models.list()
        latency_ms = round((time.time() - start) * 1000, 2)
        return {"status": "ok", "provider": "openai", "latency_ms": latency_ms}
    except Exception as e:
        return {"status": "error", "provider": "openai", "error": str(e)}


def check_disk() -> dict[str, Any]:
    """Check disk space."""
    try:
        usage = shutil.disk_usage("/")
        free_gb = round(usage.free / (1024**3), 2)
        total_gb = round(usage.total / (1024**3), 2)
        used_pct = round((usage.used / usage.total) * 100, 1)
        status = "ok" if used_pct < 90 else ("warning" if used_pct < 95 else "critical")
        return {"status": status, "free_gb": free_gb, "total_gb": total_gb, "used_pct": used_pct}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def deep_health_check() -> dict[str, Any]:
    """Run all health checks and return aggregate status."""
    db = check_database()
    llm = check_llm()
    disk = check_disk()

    checks = {"database": db, "llm": llm, "disk": disk}
    overall = "healthy"
    if any(c.get("status") == "error" for c in checks.values()):
        overall = "degraded"
    if disk.get("status") == "critical":
        overall = "unhealthy"

    return {"status": overall, "checks": checks}
