"""Route-contract tests for the versioned + review + audit endpoints."""

import io

import pytest
from fastapi.testclient import TestClient

from backend.generator import generate_batch
from backend.main import app, MAX_UPLOAD_SIZE, _rate_limiter

client = TestClient(app)


def _upload():
    data = generate_batch()
    files = {
        name: (f"{name}.csv", _csv(data[name]).encode(), "text/csv")
        for name in ("transactions", "settlements", "refunds", "bank_credits")
    }
    return client.post("/upload", files=files)


def _csv(rows):
    """Convert dict rows to a CSV string, json-encoding list/dict values."""
    if not rows:
        return ""
    import csv as _csv_mod
    import json as _json

    buf = io.StringIO()
    writer = _csv_mod.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for row in rows:
        serialized = {
            k: (_json.dumps(v) if isinstance(v, (list, dict)) else v)
            for k, v in row.items()
        }
        writer.writerow(serialized)
    return buf.getvalue()


def _job(completed=True):
    r = _upload()
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    if completed:
        status = client.get(f"/status/{job_id}")
        assert status.status_code == 200
        return job_id, status.json()
    return job_id, {}


class TestVersionedApi:
    def test_v1_health(self):
        r = client.get("/v1/health")
        assert r.status_code == 200
        assert set(r.json()["checks"]) == {"database", "llm", "disk"}

    def test_v1_prompts(self):
        r = client.get("/v1/prompts")
        assert r.status_code == 200
        assert "prompts" in r.json()

    def test_v1_jobs_pagination(self):
        _job()
        r = client.get("/v1/jobs?page=1&page_size=5")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 1
        assert len(body["items"]) == body["total"]
        assert body["total_pages"] >= 1

    def test_v1_job_results(self):
        job_id, job = _job()
        sid = job["results"][0]["settlement_id"]
        r = client.get(f"/v1/jobs/{job_id}/results?page=1&page_size=10")
        assert r.status_code == 200
        assert r.json()["total"] == job["total_settlements"]
        assert r.json()["items"][0]["settlement_id"] == sid

    def test_v1_costs_found(self):
        job_id, job = _job()
        r = client.get(f"/v1/costs/{job_id}")
        assert r.status_code == 200
        assert r.json()["job_id"] == job_id
        assert r.json()["total_settlements"] == job["total_settlements"]

    def test_v1_costs_missing(self):
        r = client.get("/v1/costs/does-not-exist")
        assert r.status_code == 404

    def test_v1_audit(self):
        upload_hash = _upload().json()["upload_hash"]
        r = client.get(f"/v1/audit/{upload_hash}?page=1&page_size=10")
        assert r.status_code == 200
        assert r.json()["total"] > 0


class TestAuditEndpoints:
    def test_audit_invalid_format_400(self):
        r = client.get("/audit/not-a-hash")
        assert r.status_code == 400

    def test_audit_not_found(self):
        r = client.get("/audit/" + "0" * 64)
        assert r.status_code == 404

    def test_audit_found(self):
        upload_hash = _upload().json()["upload_hash"]
        r = client.get(f"/audit/{upload_hash}")
        assert r.status_code == 200
        assert r.json()["total_records"] > 0

    def test_verify_valid(self):
        upload_hash = _upload().json()["upload_hash"]
        r = client.get(f"/audit/{upload_hash}/verify")
        assert r.status_code == 200
        assert r.json()["valid"] is True

    def test_verify_invalid_format(self):
        r = client.get("/audit/zz/verify")
        assert r.status_code == 400


class TestSettlementEndpoint:
    def test_invalid_format(self):
        r = client.get("/settlement/not-valid")
        assert r.status_code == 400

    def test_found(self):
        _, job = _job()
        sid = job["results"][0]["settlement_id"]
        r = client.get(f"/settlement/{sid}")
        assert r.status_code == 200
        assert r.json()["total_records"] >= 1


class TestReviewApi:
    def test_submit_and_query_decision(self):
        _, job = _job()
        sid = job["results"][0]["settlement_id"]
        r = client.post(
            f"/api/review/{sid}/decision",
            json={"decision": "APPROVE", "reason": "verified", "reviewer_id": "ops"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "accepted"
        assert body["decision"] == "APPROVE"

        status = client.get(f"/api/review/{sid}")
        assert status.status_code == 200
        assert status.json()["reviewed"] is True

    def test_invalid_decision_400(self):
        r = client.post(
            "/api/review/SETL_9999/decision",
            json={"decision": "RANDOM", "reason": "x", "reviewer_id": "ops"},
        )
        assert r.status_code == 400

    def test_pending_list(self):
        _job()
        r = client.get("/api/review/pending")
        assert r.status_code == 200
        body = r.json()
        assert "total_pending" in body
        assert isinstance(body["settlements"], list)

    def test_pending_rate_limited(self, monkeypatch):
        monkeypatch.setattr(_rate_limiter, "check_api", lambda ip: False)
        r = client.get("/api/review/pending")
        assert r.status_code == 429


class TestUploadValidation:
    def test_unsupported_content_type_415(self):
        data = generate_batch()
        files = {
            name: (f"{name}.csv", _csv(data[name]).encode(), "text/csv")
            for name in ("transactions", "settlements", "refunds", "bank_credits")
        }
        files["transactions"] = (
            "transactions.csv",
            _csv(data["transactions"]).encode(),
            "application/pdf",
        )
        r = client.post("/upload", files=files)
        assert r.status_code == 415

    def test_too_large_413(self, monkeypatch):
        data = generate_batch()
        files = {
            name: (f"{name}.csv", _csv(data[name]).encode(), "text/csv")
            for name in ("transactions", "settlements", "refunds", "bank_credits")
        }
        monkeypatch.setattr("backend.main.MAX_UPLOAD_SIZE", 10)
        r = client.post("/upload", files=files)
        assert r.status_code == 413

    def test_duplicate_upload_cached(self):
        data = generate_batch()
        files = {
            name: (f"{name}.csv", _csv(data[name]).encode(), "text/csv")
            for name in ("transactions", "settlements", "refunds", "bank_credits")
        }
        r1 = client.post("/upload", files=files)
        r2 = client.post("/upload", files=files)
        assert r1.status_code == 202
        assert r2.status_code == 202
        assert r1.json()["upload_hash"] == r2.json()["upload_hash"]