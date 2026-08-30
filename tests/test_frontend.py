"""
Phase 11 Tests: Frontend Serving

Must pass: Dashboard shows upload → results → queue → audit trace.
Tests verify frontend is served correctly by FastAPI.
"""

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from backend.main import app, _jobs

client = TestClient(app)


def _csv_bytes(rows: list[dict]) -> bytes:
    """Convert list of dicts to CSV bytes, serializing lists as JSON."""
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
    writer.writeheader()
    for row in rows:
        serialized = {}
        for k, v in row.items():
            if isinstance(v, list):
                serialized[k] = json.dumps(v)
            else:
                serialized[k] = v
        writer.writerow(serialized)
    return buf.getvalue().encode()


class TestFrontendServing:
    def setup_method(self):
        _jobs.clear()

    def test_index_returns_html(self):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Nivara" in response.text

    def test_index_contains_react(self):
        response = client.get("/")
        assert "react" in response.text.lower()
        assert "root" in response.text

    def test_index_loads_all_components(self):
        response = client.get("/")
        # Vite build: single entry point instead of individual script tags
        assert "main.jsx" in response.text
        assert "root" in response.text

    def test_app_jsx_served(self):
        response = client.get("/static/App.jsx")
        assert response.status_code == 200
        assert len(response.text) > 0

    def test_upload_panel_served(self):
        response = client.get("/static/components/UploadPanel.jsx")
        assert response.status_code == 200
        assert "UploadPanel" in response.text

    def test_results_table_served(self):
        response = client.get("/static/components/ResultsTable.jsx")
        assert response.status_code == 200
        assert "ResultsTable" in response.text
        assert "HeroMetrics" in response.text

    def test_review_queue_served(self):
        response = client.get("/static/components/ReviewQueue.jsx")
        assert response.status_code == 200
        assert "ReviewQueue" in response.text

    def test_audit_trace_served(self):
        response = client.get("/static/components/AuditTrace.jsx")
        assert response.status_code == 200
        assert "AuditTrace" in response.text

    def test_batch_patterns_served(self):
        response = client.get("/static/components/BatchPatterns.jsx")
        assert response.status_code == 200
        assert "BatchPatterns" in response.text

    def test_health_still_works(self):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestDashboardFlow:
    def setup_method(self):
        _jobs.clear()

    def test_full_upload_then_status_flow(self):
        from backend.generator import generate_batch

        data = generate_batch()

        files = {
            "transactions": ("transactions.csv", _csv_bytes(data["transactions"]), "text/csv"),
            "settlements": ("settlements.csv", _csv_bytes(data["settlements"]), "text/csv"),
            "refunds": ("refunds.csv", _csv_bytes(data["refunds"]), "text/csv"),
            "bank_credits": ("bank_credits.csv", _csv_bytes(data["bank_credits"]), "text/csv"),
        }

        # Upload
        upload_resp = client.post("/upload", files=files)
        assert upload_resp.status_code == 202
        job_id = upload_resp.json()["job_id"]

        # Status returns full dashboard data
        status_resp = client.get(f"/status/{job_id}")
        body = status_resp.json()
        assert body["status"] == "completed"

        # Hero metrics present
        assert body["total_settlements"] > 0
        assert "clean_matches" in body
        assert "exceptions" in body
        assert "unresolved" in body
        assert "ai_investigations" in body
        assert "ai_auto_approved" in body

        # Results table present
        assert len(body["results"]) == body["total_settlements"]
        for result in body["results"]:
            assert "settlement_id" in result
            assert "decision_state" in result
            assert "difference_paise" in result

        # Review queue (escalate_to_human items)
        review_items = [r for r in body["results"] if r["escalate_to_human"]]
        assert isinstance(review_items, list)

        # Batch patterns
        assert isinstance(body["batch_analysis"], list)

        # Audit trail
        assert len(body["audit_records"]) == body["total_settlements"]

    def test_reconciliation_trace_data(self):
        from backend.generator import generate_batch

        data = generate_batch()

        files = {
            "transactions": ("transactions.csv", _csv_bytes(data["transactions"]), "text/csv"),
            "settlements": ("settlements.csv", _csv_bytes(data["settlements"]), "text/csv"),
            "refunds": ("refunds.csv", _csv_bytes(data["refunds"]), "text/csv"),
            "bank_credits": ("bank_credits.csv", _csv_bytes(data["bank_credits"]), "text/csv"),
        }

        upload_resp = client.post("/upload", files=files)
        job_id = upload_resp.json()["job_id"]
        status_resp = client.get(f"/status/{job_id}")
        results = status_resp.json()["results"]

        # Pick any result — it should have trace data
        r = results[0]
        assert "expected_amount_paise" in r
        assert "actual_amount_paise" in r
        assert "deterministic_checks_passed" in r
        assert "deterministic_checks_failed" in r

        # If AI investigated, AI response has full data
        if r.get("ai_response"):
            ai = r["ai_response"]
            assert "classification" in ai
            assert "explanation" in ai
            assert "raw_confidence" in ai
            assert "cited_evidence" in ai
            assert "recommended_action" in ai

    def test_audit_trace_data(self):
        from backend.generator import generate_batch

        data = generate_batch()

        files = {
            "transactions": ("transactions.csv", _csv_bytes(data["transactions"]), "text/csv"),
            "settlements": ("settlements.csv", _csv_bytes(data["settlements"]), "text/csv"),
            "refunds": ("refunds.csv", _csv_bytes(data["refunds"]), "text/csv"),
            "bank_credits": ("bank_credits.csv", _csv_bytes(data["bank_credits"]), "text/csv"),
        }

        upload_resp = client.post("/upload", files=files)
        upload_hash = upload_resp.json()["upload_hash"]

        # Audit endpoint
        audit_resp = client.get(f"/audit/{upload_hash}")
        body = audit_resp.json()
        assert body["total_records"] > 0
        for record in body["records"]:
            assert "id" in record
            assert "upload_hash" in record
            assert "settlement_id" in record
            assert "decision_state" in record
            assert "payload_json" in record
