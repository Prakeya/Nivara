"""Tests for Razorpay live integration endpoint."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from backend.main import app
from backend.mcp_client import RazorpayMCPClient, MCPSettlement

client = TestClient(app)


class TestRazorpayEndpoint:
    def test_fetch_razorpay_not_configured(self):
        """Returns 503 when Razorpay credentials are not set."""
        with patch.object(RazorpayMCPClient, "from_env", return_value=None):
            r = client.post("/api/fetch-razorpay", json={})
            assert r.status_code == 503
            assert "not configured" in r.json()["detail"]

    def test_fetch_razorpay_empty_results(self):
        """Returns empty list when no settlements found."""
        fake_client = MagicMock(spec=RazorpayMCPClient)
        fake_client.fetch_settlements.return_value = []
        fake_client.is_available.return_value = True

        with patch.object(RazorpayMCPClient, "from_env", return_value=fake_client):
            r = client.post("/api/fetch-razorpay", json={"count": 10})
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "empty"
            assert body["settlements"] == []

    def test_fetch_razorpay_success(self):
        """Returns settlements when API call succeeds."""
        fake_settlements = [
            MCPSettlement(
                settlement_id="setl_123",
                amount=100000,
                status="settled",
                utr="UTR123456",
                created_at="2026-08-30T10:00:00Z",
                settled_at="2026-08-31T10:00:00Z",
                linked_payment_ids=["pay_1", "pay_2"],
                linked_refund_ids=[],
            ),
        ]

        fake_client = MagicMock(spec=RazorpayMCPClient)
        fake_client.fetch_settlements.return_value = fake_settlements
        fake_client.to_csv_rows.return_value = [
            {
                "settlement_id": "setl_123",
                "amount": 100000,
                "status": "settled",
                "utr": "UTR123456",
                "created_at": "2026-08-30T10:00:00Z",
                "settled_at": "2026-08-31T10:00:00Z",
                "linked_payment_ids": "['pay_1', 'pay_2']",
                "linked_refund_ids": "[]",
            },
        ]
        fake_client.is_available.return_value = True

        with patch.object(RazorpayMCPClient, "from_env", return_value=fake_client):
            r = client.post("/api/fetch-razorpay", json={"count": 5})
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "fetched"
            assert body["count"] == 1
            assert len(body["settlements"]) == 1
            assert body["settlements"][0]["settlement_id"] == "setl_123"

    def test_fetch_razorpay_with_dates(self):
        """Passes date filters to the client."""
        fake_client = MagicMock(spec=RazorpayMCPClient)
        fake_client.fetch_settlements.return_value = []
        fake_client.is_available.return_value = True

        with patch.object(RazorpayMCPClient, "from_env", return_value=fake_client):
            r = client.post("/api/fetch-razorpay", json={
                "from_date": "2026-08-01",
                "to_date": "2026-08-31",
                "count": 50,
            })
            assert r.status_code == 200
            call_kwargs = fake_client.fetch_settlements.call_args
            assert call_kwargs.kwargs["count"] == 50

    def test_fetch_razorpay_api_error(self):
        """Returns 502 when Razorpay API fails."""
        fake_client = MagicMock(spec=RazorpayMCPClient)
        fake_client.fetch_settlements.side_effect = RuntimeError("API timeout")
        fake_client.is_available.return_value = True

        with patch.object(RazorpayMCPClient, "from_env", return_value=fake_client):
            r = client.post("/api/fetch-razorpay", json={})
            assert r.status_code == 502
            assert "API timeout" in r.json()["detail"]


class TestMCPClientUnit:
    def test_from_env_missing_credentials(self):
        """Returns None when env vars are missing."""
        import os
        os.environ.pop("RAZORPAY_API_KEY", None)
        os.environ.pop("RAZORPAY_API_SECRET", None)
        assert RazorpayMCPClient.from_env() is None

    def test_from_env_with_credentials(self):
        """Returns client when env vars are set."""
        import os
        os.environ["RAZORPAY_API_KEY"] = "test_key"
        os.environ["RAZORPAY_API_SECRET"] = "test_secret"
        try:
            client = RazorpayMCPClient.from_env()
            assert client is not None
            assert client.is_available()
        finally:
            os.environ.pop("RAZORPAY_API_KEY", None)
            os.environ.pop("RAZORPAY_API_SECRET", None)

    def test_to_csv_rows(self):
        """Converts MCPSettlement to CSV-compatible dicts."""
        settlements = [
            MCPSettlement(
                settlement_id="setl_001",
                amount=50000,
                status="settled",
                utr="UTR001",
                created_at="2026-08-30T10:00:00Z",
                settled_at="2026-08-31T10:00:00Z",
                linked_payment_ids=["pay_a"],
                linked_refund_ids=[],
            ),
        ]
        client = RazorpayMCPClient("key", "secret")
        rows = client.to_csv_rows(settlements)
        assert len(rows) == 1
        assert rows[0]["settlement_id"] == "setl_001"
        assert rows[0]["amount"] == 50000
