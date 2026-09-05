"""Shared helpers used across multiple backend.routes modules.

Extracted from backend/main.py (Task 7 router split). Unlike job_store.py,
this module is allowed to import from other backend modules (audit, groq
client, etc.) — it just must not import from backend.routes, to avoid
circular imports.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import Request

from backend.audit import AuditLogger

# ---------------------------------------------------------------------------
# Audit DB path (persistent, survives restart)
# ---------------------------------------------------------------------------

_AUDIT_DB_DIR = Path("data/audit")
_AUDIT_DB_PATH = _AUDIT_DB_DIR / "audit.db"


def _get_audit_logger() -> AuditLogger:
    """Return an AuditLogger backed by the persistent SQLite DB."""
    _AUDIT_DB_DIR.mkdir(parents=True, exist_ok=True)
    return AuditLogger(str(_AUDIT_DB_PATH))


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_llm_client() -> Optional[str]:
    """Return a truthy value if Groq is available.

    The architecture uses a Groq-first fallback chain (70B -> 8B -> UNRESOLVED).
    Returns "configured" when GROQ_API_KEY is set, which signals to run_engine
    that AI investigation should be enabled.
    """
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        return "configured"
    return None


def _validate_llm_config() -> None:
    """
    Fail-fast GROQ_API_KEY validation at startup (WATCH-G4).

    Raises:
        RuntimeError: when GROQ_API_KEY is missing/empty or the Groq SDK is
        unavailable. The deterministic engine still works, but AI investigation
        (MATH_DISCREPANCY) would be permanently UNRESOLVED — surface loudly at
        boot instead. Key validity is confirmed on the first real API call.
    """
    from backend.groq_client import GroqClient

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not groq_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Nivara requires a Groq API key for AI "
            "investigation. Set GROQ_API_KEY in the environment or a .env file."
        )
    # Instantiation fails fast if the groq SDK is not installed.
    GroqClient(api_key=groq_key)


def _compute_hash(file_paths: list[str]) -> str:
    """Compute SHA-256 hash using canonical ingestion hash (sort CSVs by first column)."""
    from backend.ingestion import compute_upload_hash
    return compute_upload_hash(file_paths)
