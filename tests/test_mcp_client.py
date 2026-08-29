"""Tests for Razorpay MCP Client."""

from backend.mcp_client import RazorpayMCPClient, MCPSettlement


class TestMCPClient:
    def test_from_env_returns_none_without_credentials(self):
        import os
        old_key = os.environ.pop("RAZORPAY_API_KEY", None)
        old_secret = os.environ.pop("RAZORPAY_API_SECRET", None)
        try:
            client = RazorpayMCPClient.from_env()
            assert client is None
        finally:
            if old_key:
                os.environ["RAZORPAY_API_KEY"] = old_key
            if old_secret:
                os.environ["RAZORPAY_API_SECRET"] = old_secret

    def test_from_env_returns_client_with_credentials(self):
        import os
        old_key = os.environ.get("RAZORPAY_API_KEY")
        old_secret = os.environ.get("RAZORPAY_API_SECRET")
        os.environ["RAZORPAY_API_KEY"] = "test-placeholder-key-NOT-REAL-12345"
        os.environ["RAZORPAY_API_SECRET"] = "test-placeholder-secret-NOT-REAL-67890"
        try:
            client = RazorpayMCPClient.from_env()
            assert client is not None
            assert client.is_available()
        finally:
            if old_key:
                os.environ["RAZORPAY_API_KEY"] = old_key
            else:
                os.environ.pop("RAZORPAY_API_KEY", None)
            if old_secret:
                os.environ["RAZORPAY_API_SECRET"] = old_secret
            else:
                os.environ.pop("RAZORPAY_API_SECRET", None)

    def test_to_csv_rows(self):
        client = RazorpayMCPClient("key", "secret")
        settlements = [
            MCPSettlement(
                settlement_id="SETL_001",
                amount=50000,
                status="settled",
                utr="UTR_001",
                created_at="2026-08-20T10:00:00",
                settled_at="2026-08-21T10:00:00",
                linked_payment_ids=["PAY_001"],
                linked_refund_ids=[],
            )
        ]
        rows = client.to_csv_rows(settlements)
        assert len(rows) == 1
        assert rows[0]["settlement_id"] == "SETL_001"
        assert rows[0]["amount"] == 50000

    def test_fetch_settlements_raises_without_config(self):
        client = RazorpayMCPClient("", "")
        import pytest
        with pytest.raises(RuntimeError, match="not configured"):
            client.fetch_settlements()
