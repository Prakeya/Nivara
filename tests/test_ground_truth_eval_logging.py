"""
Task 3 regression tests: ground-truth evaluation exception handling.

backend/main.py's upload pipeline loads an optional ground_truth.json and runs
evaluate_batch() against it. That step is best-effort: a missing ground truth
file is expected/benign, while a malformed file or a bug in evaluate_batch is a
real failure. Both used to be swallowed identically by a bare
`except Exception: logger.warning(...)`. This file verifies the two cases are
now handled distinctly:
  - FileNotFoundError -> quiet logger.info, evaluation skipped, job still completes.
  - Any other exception -> logger.exception (captures traceback), job still
    completes non-fatally, but the log is visibly different from the
    FileNotFoundError case.
"""

import builtins
import csv
import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

from backend.main import app, _jobs
from backend.generator import generate_batch

client = TestClient(app)


def _csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
    writer.writeheader()
    for row in rows:
        serialized = {}
        for k, v in row.items():
            serialized[k] = json.dumps(v) if isinstance(v, list) else v
        writer.writerow(serialized)
    return buf.getvalue().encode()


def _make_upload_files(data: dict):
    return {
        "transactions": ("transactions.csv", _csv_bytes(data["transactions"]), "text/csv"),
        "settlements": ("settlements.csv", _csv_bytes(data["settlements"]), "text/csv"),
        "refunds": ("refunds.csv", _csv_bytes(data["refunds"]), "text/csv"),
        "bank_credits": ("bank_credits.csv", _csv_bytes(data["bank_credits"]), "text/csv"),
    }


class TestGroundTruthEvaluationExceptionHandling:
    def setup_method(self):
        _jobs.clear()

    def test_missing_ground_truth_file_is_quiet_and_nonfatal(self, monkeypatch, caplog):
        """FileNotFoundError path: os.path.exists() says the file is there (race
        condition simulation), but open() raises FileNotFoundError. Evaluation
        should be skipped quietly (info-level, no warning/error), and the job
        should still complete successfully with no evaluation results (match_rate
        stays at its default 0.0).
        """
        data = generate_batch()
        files = _make_upload_files(data)

        monkeypatch.setattr("backend.main.os.path.exists", lambda _p: True)

        real_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if isinstance(path, str) and path.endswith("ground_truth.json"):
                raise FileNotFoundError(path)
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", fake_open)

        with caplog.at_level(logging.INFO, logger="nivara.api"):
            upload_resp = client.post("/upload", files=files)

        assert upload_resp.status_code == 202
        job_id = upload_resp.json()["job_id"]

        status_resp = client.get(f"/status/{job_id}")
        assert status_resp.status_code == 200
        body = status_resp.json()
        assert body["status"] == "completed"
        assert body["match_rate"] == 0.0

        gt_records = [r for r in caplog.records if "Ground truth" in r.message or "ground truth" in r.message.lower()]
        assert gt_records, "expected a ground-truth related log record"
        assert all(r.levelno == logging.INFO for r in gt_records), (
            f"FileNotFoundError case should log at INFO only, got levels: "
            f"{[r.levelname for r in gt_records]}"
        )
        assert not any(r.exc_info for r in gt_records), "FileNotFoundError case should not attach a traceback"

    def test_evaluate_batch_failure_is_logged_distinctly_and_nonfatal(self, monkeypatch, caplog):
        """Real failure path: evaluate_batch raises a ValueError (simulating a bug
        or malformed data). This must be logged with logger.exception (traceback
        captured, ERROR level) and be visibly distinct from the FileNotFoundError
        message, while the upload/job flow still completes non-fatally.
        """
        # generate_batch() defaults to 80 settlements, matching the 80-row
        # data/evaluation/ground_truth.json checked into the repo, so the
        # len(gt_list) == len(results) guard passes and evaluate_batch is called.
        data = generate_batch()
        files = _make_upload_files(data)

        def boom(*args, **kwargs):
            raise ValueError("simulated evaluate_batch failure")

        monkeypatch.setattr("backend.evaluation.evaluate_batch", boom)

        with caplog.at_level(logging.INFO, logger="nivara.api"):
            upload_resp = client.post("/upload", files=files)

        assert upload_resp.status_code == 202
        job_id = upload_resp.json()["job_id"]

        status_resp = client.get(f"/status/{job_id}")
        assert status_resp.status_code == 200
        body = status_resp.json()
        assert body["status"] == "completed"
        assert body["match_rate"] == 0.0

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records, "expected an ERROR-level log record for the real failure"
        assert any(r.exc_info for r in error_records), "real failure should be logged with logger.exception (traceback)"
        assert any("Ground truth" in r.message for r in error_records)

        # Distinct message from the FileNotFoundError branch.
        assert not any("not found" in r.message.lower() for r in error_records)
