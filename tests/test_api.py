"""
Phase 10 Tests: FastAPI Endpoints

Must pass: API accepts multipart upload. Returns job ID. Status endpoint returns results.
"""

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from backend.main import app, _jobs
from backend.generator import generate_batch

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _csv_bytes(rows: list[dict]) -> bytes:
    """Convert list of dicts to CSV bytes."""
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


def _make_upload_files(data: dict):
    """Create upload file tuples from generator output."""
    return {
        "transactions": ("transactions.csv", _csv_bytes(data["transactions"]), "text/csv"),
        "settlements": ("settlements.csv", _csv_bytes(data["settlements"]), "text/csv"),
        "refunds": ("refunds.csv", _csv_bytes(data["refunds"]), "text/csv"),
        "bank_credits": ("bank_credits.csv", _csv_bytes(data["bank_credits"]), "text/csv"),
    }


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["version"] == "0.1.0"


# ---------------------------------------------------------------------------
# Upload + Status flow
# ---------------------------------------------------------------------------

class TestUploadAndStatus:
    def setup_method(self):
        _jobs.clear()

    def test_upload_returns_job_id(self):
        data = generate_batch()
        files = _make_upload_files(data)

        response = client.post("/upload", files=files)
        assert response.status_code == 202

        body = response.json()
        assert "job_id" in body
        assert "upload_hash" in body
        assert body["status"] == "completed"

    def test_status_returns_results(self):
        data = generate_batch()
        files = _make_upload_files(data)

        upload_resp = client.post("/upload", files=files)
        job_id = upload_resp.json()["job_id"]

        status_resp = client.get(f"/status/{job_id}")
        assert status_resp.status_code == 200

        body = status_resp.json()
        assert body["status"] == "completed"
        assert body["total_settlements"] > 0
        assert len(body["results"]) == body["total_settlements"]
        assert "clean_matches" in body
        assert "exceptions" in body
        assert "unresolved" in body
        assert "ai_investigations" in body

    def test_status_404_for_unknown_job(self):
        response = client.get("/status/nonexistent")
        assert response.status_code == 404

    def test_upload_then_status_counts_match(self):
        data = generate_batch()
        files = _make_upload_files(data)

        upload_resp = client.post("/upload", files=files)
        job_id = upload_resp.json()["job_id"]

        status_resp = client.get(f"/status/{job_id}")
        body = status_resp.json()

        # All settlements should be accounted for
        assert body["total_settlements"] > 0
        total = (
            body["clean_matches"]
            + body["exceptions"]
            + body["math_discrepancies"]
            + body["unresolved"]
        )
        assert total == body["total_settlements"]

    def test_audit_records_populated(self):
        data = generate_batch()
        files = _make_upload_files(data)

        upload_resp = client.post("/upload", files=files)
        job_id = upload_resp.json()["job_id"]

        status_resp = client.get(f"/status/{job_id}")
        body = status_resp.json()

        assert len(body["audit_records"]) == body["total_settlements"]
        for record in body["audit_records"]:
            assert "id" in record
            assert "upload_hash" in record
            assert "settlement_id" in record
            assert "decision_state" in record

    def test_batch_analysis_populated(self):
        data = generate_batch()
        files = _make_upload_files(data)

        upload_resp = client.post("/upload", files=files)
        job_id = upload_resp.json()["job_id"]

        status_resp = client.get(f"/status/{job_id}")
        body = status_resp.json()

        assert "batch_analysis" in body
        assert isinstance(body["batch_analysis"], list)

    def test_results_have_decision_states(self):
        data = generate_batch()
        files = _make_upload_files(data)

        upload_resp = client.post("/upload", files=files)
        job_id = upload_resp.json()["job_id"]

        status_resp = client.get(f"/status/{job_id}")
        body = status_resp.json()

        valid_states = {
            "CLEAN_MATCH", "DETERMINISTIC_EXCEPTION", "MATH_DISCREPANCY",
            "REVIEW_REQUIRED", "UNPROCESSED", "UNRESOLVED",
        }
        for result in body["results"]:
            assert result["decision_state"] in valid_states
            assert "settlement_id" in result
            assert "difference_paise" in result


# ---------------------------------------------------------------------------
# Audit endpoint
# ---------------------------------------------------------------------------

class TestAuditEndpoint:
    def setup_method(self):
        _jobs.clear()

    def test_audit_returns_records(self):
        data = generate_batch()
        files = _make_upload_files(data)

        upload_resp = client.post("/upload", files=files)
        upload_hash = upload_resp.json()["upload_hash"]

        audit_resp = client.get(f"/audit/{upload_hash}")
        assert audit_resp.status_code == 200

        body = audit_resp.json()
        assert body["upload_hash"] == upload_hash
        assert body["total_records"] > 0
        assert len(body["records"]) == body["total_records"]

    def test_audit_404_for_unknown_hash(self):
        response = client.get("/audit/nonexistent")
        assert response.status_code == 404

    def test_audit_records_are_append_only(self):
        data = generate_batch()
        files = _make_upload_files(data)

        upload_resp = client.post("/upload", files=files)
        upload_hash = upload_resp.json()["upload_hash"]

        audit_resp = client.get(f"/audit/{upload_hash}")
        records = audit_resp.json()["records"]

        # All records have the same upload_hash
        for record in records:
            assert record["upload_hash"] == upload_hash

        # Records have timestamps in insertion order (append-only)
        timestamps = [r["timestamp"] for r in records]
        assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# Settlement endpoint
# ---------------------------------------------------------------------------

class TestSettlementEndpoint:
    def setup_method(self):
        _jobs.clear()

    def test_settlement_returns_history(self):
        data = generate_batch()
        files = _make_upload_files(data)

        upload_resp = client.post("/upload", files=files)

        # Get a settlement_id from the results
        job_id = upload_resp.json()["job_id"]
        status_resp = client.get(f"/status/{job_id}")
        first_result = status_resp.json()["results"][0]
        sid = first_result["settlement_id"]

        settlement_resp = client.get(f"/settlement/{sid}")
        assert settlement_resp.status_code == 200

        body = settlement_resp.json()
        assert body["settlement_id"] == sid
        assert body["total_records"] >= 1

    def test_settlement_empty_for_unknown(self):
        response = client.get("/settlement/NONEXISTENT")
        assert response.status_code == 200
        body = response.json()
        assert body["total_records"] == 0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def setup_method(self):
        _jobs.clear()

    def test_upload_invalid_csv_returns_error(self):
        bad_csv = b"not,valid,csv,data\n"
        files = {
            "transactions": ("transactions.csv", bad_csv, "text/csv"),
            "settlements": ("settlements.csv", bad_csv, "text/csv"),
            "refunds": ("refunds.csv", bad_csv, "text/csv"),
            "bank_credits": ("bank_credits.csv", bad_csv, "text/csv"),
        }
        response = client.post("/upload", files=files)
        assert response.status_code == 202
        body = response.json()
        assert "job_id" in body
        # Engine processes whatever it can — may succeed with 0 results or error
        status_resp = client.get(f"/status/{body['job_id']}")
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] in ("completed", "error")


# ---------------------------------------------------------------------------
# Audit persistence after restart
# ---------------------------------------------------------------------------

class TestAuditPersistence:
    def test_audit_survives_inmemory_clear(self):
        """Audit records persist in SQLite even after in-memory state is cleared."""
        data = generate_batch()
        files = _make_upload_files(data)

        upload_resp = client.post("/upload", files=files)
        upload_hash = upload_resp.json()["upload_hash"]

        # Verify audit exists
        audit_resp = client.get(f"/audit/{upload_hash}")
        assert audit_resp.status_code == 200
        assert audit_resp.json()["total_records"] > 0

        # Clear in-memory job store (simulates partial restart)
        _jobs.clear()

        # Audit endpoint still works (reads from SQLite)
        audit_resp2 = client.get(f"/audit/{upload_hash}")
        assert audit_resp2.status_code == 200
        assert audit_resp2.json()["total_records"] > 0

    def test_audit_survives_full_restart_simulation(self):
        """Audit records survive complete in-memory state reset."""
        data = generate_batch()
        files = _make_upload_files(data)

        upload_resp = client.post("/upload", files=files)
        upload_hash = upload_resp.json()["upload_hash"]

        # Simulate full restart: clear ALL in-memory state
        _jobs.clear()

        # Audit endpoint reads directly from SQLite
        audit_resp = client.get(f"/audit/{upload_hash}")
        assert audit_resp.status_code == 200
        records = audit_resp.json()["records"]
        assert len(records) > 0

        # Verify record structure
        for record in records:
            assert record["upload_hash"] == upload_hash
            assert "settlement_id" in record
            assert "decision_state" in record

    def test_settlement_history_survives_restart(self):
        """Settlement audit history survives in-memory state reset."""
        data = generate_batch()
        files = _make_upload_files(data)

        upload_resp = client.post("/upload", files=files)
        job_id = upload_resp.json()["job_id"]

        # Get a settlement ID
        status_resp = client.get(f"/status/{job_id}")
        sid = status_resp.json()["results"][0]["settlement_id"]

        # Clear in-memory state
        _jobs.clear()

        # Settlement endpoint reads from SQLite
        settlement_resp = client.get(f"/settlement/{sid}")
        assert settlement_resp.status_code == 200
        assert settlement_resp.json()["total_records"] >= 1
