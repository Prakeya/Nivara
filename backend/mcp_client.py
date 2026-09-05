"""
Phase 16: Razorpay MCP Client (Optional Live Data Source)

Provides an optional integration with Razorpay's settlement API to fetch
settlement data live instead of requiring CSV upload. Falls back to CSV
upload when credentials are not configured.

Usage:
    from backend.mcp_client import RazorpayMCPClient

    client = RazorpayMCPClient.from_env()
    if client.is_available():
        settlements = client.fetch_settlements(from_date, to_date)
    else:
        # Fall back to CSV upload
        pass

Safety: This client is READ-ONLY. It never modifies settlement data.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

logger = logging.getLogger("nivara.mcp_client")


@dataclass
class MCPSettlement:
    """Settlement record fetched from Razorpay MCP."""
    settlement_id: str
    amount: int
    status: str
    utr: str
    created_at: str
    settled_at: str
    linked_payment_ids: list[str]
    linked_refund_ids: list[str]


class RazorpayMCPClient:
    """Client for Razorpay settlement API (MCP-compatible).

    When RAZORPAY_API_KEY and RAZORPAY_API_SECRET are set, this client
    can fetch settlements directly from Razorpay. When credentials are
    missing, is_available() returns False and the system falls back to CSV.

    This is a partial integration demonstrating real Razorpay alignment.
    Full MCP protocol support would require the MCP SDK.
    """

    def __init__(self, api_key: str, api_secret: str):
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = "https://api.razorpay.com/v1"

    @classmethod
    def from_env(cls) -> Optional["RazorpayMCPClient"]:
        """Create client from environment variables. Returns None if unconfigured."""
        api_key = os.environ.get("RAZORPAY_API_KEY")
        api_secret = os.environ.get("RAZORPAY_API_SECRET")
        if not api_key or not api_secret:
            return None
        try:
            return cls(api_key=api_key, api_secret=api_secret)
        except Exception:
            logger.exception("Failed to construct RazorpayMCPClient from environment credentials")
            return None

    def is_available(self) -> bool:
        """Check if the client is configured and ready to use."""
        return bool(self._api_key and self._api_secret)

    def fetch_settlements(
        self,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        count: int = 100,
    ) -> list[MCPSettlement]:
        """Fetch settlements from Razorpay API.

        Args:
            from_date: Start date filter (inclusive).
            to_date: End date filter (inclusive).
            count: Maximum number of settlements to fetch.

        Returns:
            List of MCPSettlement records.

        Raises:
            RuntimeError: If the API call fails.
        """
        if not self.is_available():
            raise RuntimeError("Razorpay MCP client not configured")

        try:
            import httpx
        except ImportError:
            raise RuntimeError("httpx not installed. Run: pip install httpx")

        params: dict[str, Any] = {"count": count}
        if from_date:
            params["from"] = int(datetime.combine(from_date, datetime.min.time()).timestamp())
        if to_date:
            params["to"] = int(datetime.combine(to_date, datetime.max.time()).timestamp())

        try:
            response = httpx.get(
                f"{self._base_url}/settlements",
                auth=(self._api_key, self._api_secret),
                params=params,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise RuntimeError(f"Razorpay API error: {e}")

        settlements = []
        for item in data.get("items", []):
            settlements.append(MCPSettlement(
                settlement_id=item.get("id", ""),
                amount=item.get("amount", 0),
                status=item.get("status", "pending"),
                utr=item.get("utr", ""),
                created_at=item.get("created_at", ""),
                settled_at=item.get("settled_at", ""),
                linked_payment_ids=item.get("payments", []),
                linked_refund_ids=item.get("refunds", []),
            ))

        return settlements

    def _fetch_items(self, resource: str, count: int = 100) -> list[dict[str, Any]]:
        """Fetch a read-only Razorpay collection used for reconciliation."""
        if not self.is_available():
            raise RuntimeError("Razorpay MCP client not configured")
        try:
            import httpx
            response = httpx.get(
                f"{self._base_url}/{resource}",
                auth=(self._api_key, self._api_secret),
                params={"count": count},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json().get("items", [])
        except Exception as exc:
            raise RuntimeError(f"Razorpay {resource} API error: {exc}") from exc

    def fetch_payments(self, count: int = 100) -> list[dict[str, Any]]:
        return self._fetch_items("payments", count)

    def fetch_refunds(self, count: int = 100) -> list[dict[str, Any]]:
        return self._fetch_items("refunds", count)

    def fetch_transfers(self, count: int = 100) -> list[dict[str, Any]]:
        return self._fetch_items("transfers", count)

    def to_csv_rows(self, settlements: list[MCPSettlement]) -> list[dict[str, Any]]:
        """Convert MCP settlements to CSV-compatible dicts for ingestion."""
        return [
            {
                "settlement_id": s.settlement_id,
                "amount": s.amount,
                "status": s.status,
                "utr": s.utr,
                "created_at": s.created_at,
                "settled_at": s.settled_at,
                "linked_payment_ids": str(s.linked_payment_ids),
                "linked_refund_ids": str(s.linked_refund_ids),
            }
            for s in settlements
        ]
