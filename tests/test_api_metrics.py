"""
Phase 3 STEP 3.3: GET /api/metrics + in-memory Groq/LLM trackers.

Ensures the dashboard payload stays schema-stable and the trackers that back
the quota progress bar / latency / error-rate cards accumulate correctly.
"""

import csv
import io
import json

from fastapi.testclient import TestClient

from backend.main import app, _jobs
from backend.metrics import (
    record_groq_usage,
    groq_daily_usage_snapshot,
    record_llm_call_metric,
    llm_metrics_snapshot,
)

client = TestClient(app)


def _csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for row in rows:
        serialized = {
            k: json.dumps(v) if isinstance(v, list) else v
            for k, v in row.items()
        }
        writer.writerow(serialized)
    return buf.getvalue().encode()


class TestMetricsEndpoint:
    def setup_method(self):
        _jobs.clear()

    def test_empty_state_schema(self):
        response = client.get("/api/metrics")
        assert response.status_code == 200
        body = response.json()
        assert body["batches_processed"] == 0
        assert body["settlements_processed"] == 0
        assert body["error_rate"] == 0.0
        assert body["decision_breakdown"] == {
            "clean": 0,
            "exceptions": 0,
            "math_discrepancy": 0,
            "unresolved": 0,
        }
        assert body["ai_auto_approved_total"] == 0
        assert body["groq_free_tier"]["daily_limit"] > 0
        assert body["groq_free_tier"]["pct_used"] == 0.0
        assert set(body["llm"]) == {"total_calls", "errors", "avg_latency_ms", "error_rate"}
        assert isinstance(body["generated_at"], str)

    def test_payload_after_upload(self):
        from backend.generator import generate_batch

        data = generate_batch(n_settlements=10, edge_cases={"clean_match": 10}, seed=1)
        files = {
            "transactions": ("transactions.csv", _csv_bytes(data["transactions"]), "text/csv"),
            "settlements": ("settlements.csv", _csv_bytes(data["settlements"]), "text/csv"),
            "refunds": ("refunds.csv", _csv_bytes(data["refunds"]), "text/csv"),
            "bank_credits": ("bank_credits.csv", _csv_bytes(data["bank_credits"]), "text/csv"),
        }
        upload = client.post("/upload", files=files)
        assert upload.status_code == 202

        body = client.get("/api/metrics").json()
        assert body["batches_processed"] == 1
        assert body["settlements_processed"] == 10
        assert body["decision_breakdown"]["clean"] == 10
        assert body["ai_investigations_total"] == 0


class TestMetricTrackers:
    def test_groq_usage_accumulates(self):
        before = groq_daily_usage_snapshot()
        record_groq_usage(1000, "llama-3.1-70b-versatile")
        record_groq_usage(500, "llama-3.1-70b-versatile")
        after = groq_daily_usage_snapshot()
        assert after["used_tokens"] - before["used_tokens"] == 1500
        assert after["remaining_tokens"] == max(0, after["daily_limit"] - after["used_tokens"])
        assert after["pct_used"] >= 0.0

    def test_llm_metrics_accumulate_and_track_errors(self):
        before = llm_metrics_snapshot()
        record_llm_call_metric("ok", 200.0)
        record_llm_call_metric("error", 150.0)
        after = llm_metrics_snapshot()
        assert after["total_calls"] - before["total_calls"] == 2
        assert after["errors"] - before["errors"] == 1
        assert after["avg_latency_ms"] > 0.0

    def test_groq_client_records_usage_on_success(self):
        from unittest.mock import MagicMock
        from backend.groq_client import GroqClient, DEFAULT_MODEL

        before = groq_daily_usage_snapshot()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "{}"
        mock_response.usage.prompt_tokens = 120
        mock_response.usage.completion_tokens = 40
        mock_sdk = MagicMock()
        mock_sdk.chat.completions.create.return_value = mock_response

        client = GroqClient(api_key="gsk_test_key")
        client._client = mock_sdk
        client.complete([{"role": "user", "content": "hi"}])

        after = groq_daily_usage_snapshot()
        assert after["used_tokens"] - before["used_tokens"] == 160
        assert after["by_model"].get(DEFAULT_MODEL, 0) - before["by_model"].get(DEFAULT_MODEL, 0) == 160