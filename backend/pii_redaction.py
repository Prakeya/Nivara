"""
PII redaction: masks sensitive data in logs and API responses.

Redacts: email, phone, UPI ID, bank account numbers, PAN, Aadhaar.
"""

from __future__ import annotations

import re
from typing import Any


# PII patterns
_PATTERNS = {
    "email": (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "[REDACTED_EMAIL]"),
    "phone": (re.compile(r"\b\d{10}\b"), "[REDACTED_PHONE]"),
    "upi_id": (re.compile(r"[a-zA-Z0-9._-]+@[a-zA-Z]+"), "[REDACTED_UPI]"),
    "pan": (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[REDACTED_PAN]"),
    "aadhaar": (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), "[REDACTED_AADHAAR]"),
    "bank_account": (re.compile(r"\b\d{9,18}\b"), "[REDACTED_ACCOUNT]"),
    "ifsc": (re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"), "[REDACTED_IFSC]"),
}


def redact_pii(text: str) -> str:
    """Redact all PII patterns from a string."""
    if not isinstance(text, str):
        return text
    for name, (pattern, replacement) in _PATTERNS.items():
        text = pattern.sub(replacement, text)
    return text


def redact_dict(data: dict[str, Any], fields: set[str] | None = None) -> dict[str, Any]:
    """Redact PII from specific fields in a dictionary."""
    if fields is None:
        fields = {"reason", "explanation", "reviewer_id", "notes"}
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key in fields and isinstance(value, str):
            result[key] = redact_pii(value)
        elif isinstance(value, dict):
            result[key] = redact_dict(value, fields)
        elif isinstance(value, list):
            result[key] = [
                redact_dict(item, fields) if isinstance(item, dict)
                else redact_pii(item) if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            result[key] = value
    return result


def redact_log_message(message: str) -> str:
    """Redact PII from log messages."""
    return redact_pii(message)
