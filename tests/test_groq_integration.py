"""
Groq main-integration tests (STEP 6).

Covers:
- _validate_llm_config: fail-fast on missing GROQ_API_KEY
- Startup hook registered on the FastAPI app
- Upload endpoint rejects batches over the Groq free-tier budget (422)
- health.check_llm points at Groq
- requirements.txt declares groq
"""

from __future__ import annotations

import importlib
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app, _validate_llm_config


class TestValidateLlmConfig:
    def test_missing_key_raises(self) -> None:
        old = os.environ.pop("GROQ_API_KEY", None)
        try:
            with pytest.raises(RuntimeError, match="GROQ_API_KEY is not set"):
                _validate_llm_config()
        finally:
            if old is not None:
                os.environ["GROQ_API_KEY"] = old

    def test_empty_key_raises(self) -> None:
        old = os.environ.get("GROQ_API_KEY")
        os.environ["GROQ_API_KEY"] = "   "
        try:
            with pytest.raises(RuntimeError, match="GROQ_API_KEY is not set"):
                _validate_llm_config()
        finally:
            if old is not None:
                os.environ["GROQ_API_KEY"] = old
            else:
                os.environ.pop("GROQ_API_KEY", None)

    def test_present_key_passes(self) -> None:
        old = os.environ.get("GROQ_API_KEY")
        os.environ["GROQ_API_KEY"] = "gsk-test-key"
        try:
            _validate_llm_config()  # should not raise
        finally:
            if old is not None:
                os.environ["GROQ_API_KEY"] = old
            else:
                os.environ.pop("GROQ_API_KEY", None)


class TestStartupHook:
    def test_lifespan_registered(self) -> None:
        assert app.router.lifespan_context is not None

    def test_app_serves_with_key_set(self) -> None:
        old = os.environ.get("GROQ_API_KEY")
        os.environ["GROQ_API_KEY"] = "gsk-test-key"
        try:
            with TestClient(app) as client:
                r = client.get("/health")
                # /health now runs the deep health check (DB, LLM, disk); with a
                # fake GROQ_API_KEY the LLM check legitimately fails (503), so
                # this just asserts the app serves the deep-check response.
                assert r.status_code in (200, 503)
                assert "status" in r.json()
        finally:
            if old is not None:
                os.environ["GROQ_API_KEY"] = old
            else:
                os.environ.pop("GROQ_API_KEY", None)


class TestUploadBudgetRejection:
    @patch("backend.groq_client.check_batch_feasible")
    def test_infeasible_batch_rejected_422(self, mock_feasible) -> None:
        mock_feasible.side_effect = ValueError("exceeding remaining daily limit")
        old = os.environ.get("GROQ_API_KEY")
        os.environ["GROQ_API_KEY"] = "gsk-test-key"
        try:
            with TestClient(app) as client:
                r = client.post(
                    "/upload",
                    files={
                        "transactions": ("t.csv", b"payment_id,amount\np1,100\n", "text/csv"),
                        "settlements": ("s.csv", b"settlement_id,amount,status,utr,created_at,settled_at,linked_payment_ids,linked_refund_ids\ns1,100,settled,U1,2026-08-20T10:00:00,2026-08-21T08:00:00,[\"p1\"],[]\n", "text/csv"),
                        "refunds": ("r.csv", b"refund_id,amount,payment_id,status,created_at\n", "text/csv"),
                        "bank_credits": ("b.csv", b"utr,amount,date\nU1,100,2026-08-21\n", "text/csv"),
                    },
                )
                assert r.status_code == 422
                assert "Groq free-tier budget" in r.json()["detail"]
        finally:
            if old is not None:
                os.environ["GROQ_API_KEY"] = old
            else:
                os.environ.pop("GROQ_API_KEY", None)


class TestHealthGroq:
    def test_llm_check_not_configured(self) -> None:
        old = os.environ.pop("GROQ_API_KEY", None)
        try:
            from backend.health import check_llm
            result = check_llm()
            assert result["provider"] == "none"
            assert result["status"] == "not_configured"
        finally:
            if old is not None:
                os.environ["GROQ_API_KEY"] = old

    def test_llm_check_uses_groq(self) -> None:
        from backend.health import check_llm

        old = os.environ.get("GROQ_API_KEY")
        os.environ["GROQ_API_KEY"] = "gsk-test-key"
        try:
            result = check_llm()
            assert result["provider"] == "groq"
        finally:
            if old is not None:
                os.environ["GROQ_API_KEY"] = old
            else:
                os.environ.pop("GROQ_API_KEY", None)


class TestRequirements:
    def test_groq_in_requirements(self) -> None:
        req_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "requirements.txt")
        with open(req_path) as f:
            content = f.read()
        import re
        assert re.search(r"^groq>=", content, re.MULTILINE)