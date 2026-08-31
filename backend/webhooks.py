"""
Webhook support: notify external systems on events (batch complete, review submitted).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
from typing import Any, Optional
from urllib.request import Request, urlopen

logger = logging.getLogger("nivara.webhooks")

WEBHOOK_SECRET = os.environ.get("NIVARA_WEBHOOK_SECRET", "")
WEBHOOK_URLS: list[str] = []


def register_webhook(url: str) -> None:
    """Register a webhook URL."""
    if url not in WEBHOOK_URLS:
        WEBHOOK_URLS.append(url)


def unregister_webhook(url: str) -> None:
    """Unregister a webhook URL."""
    WEBHOOK_URLS[:] = [u for u in WEBHOOK_URLS if u != url]


def _sign_payload(payload: bytes) -> str:
    """Sign payload with HMAC-SHA256."""
    if not WEBHOOK_SECRET:
        return ""
    return hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()


def _send_webhook(event: str, data: dict[str, Any], url: str) -> bool:
    """Send a webhook to a single URL."""
    payload = json.dumps({"event": event, "data": data}, default=str).encode()
    signature = _sign_payload(payload)

    headers = {
        "Content-Type": "application/json",
        "X-Nivara-Event": event,
    }
    if signature:
        headers["X-Nivara-Signature"] = signature

    try:
        req = Request(url, data=payload, headers=headers, method="POST")
        with urlopen(req, timeout=10) as resp:  # nosec B310
            return resp.status < 400
    except Exception as e:
        logger.warning("Webhook delivery failed to %s: %s", url, e)
        return False


def dispatch_webhook(event: str, data: dict[str, Any]) -> None:
    """Dispatch webhook to all registered URLs in background."""
    if not WEBHOOK_URLS:
        return

    def _send_all():
        for url in WEBHOOK_URLS:
            _send_webhook(event, data, url)

    threading.Thread(target=_send_all, daemon=True).start()


# Predefined events
EVENT_BATCH_COMPLETE = "batch.complete"
EVENT_REVIEW_SUBMITTED = "review.submitted"
EVENT_SETTLEMENT_ESCALATED = "settlement.escalated"
