"""Coverage for backend/health.py: deep health checks (DB, LLM, disk)."""

from backend import health
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


class TestCheckDatabase:
    def test_sqlite_ok(self):
        result = health.check_database()
        assert result["status"] == "ok"
        assert result["backend"] in ("sqlite", "postgres")
        assert "latency_ms" in result

    def test_db_error(self, monkeypatch):
        import backend.database as database

        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(database, "get_connection", _boom)
        result = health.check_database()
        assert result["status"] == "error"
        assert "db down" in result["error"]


class TestCheckLlm:
    def test_not_configured(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        result = health.check_llm()
        assert result == {"status": "not_configured", "provider": "none"}

    def test_ok(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "sk-dummy")

        import backend.groq_client as groq_client

        class _Models:
            def list(self):
                return []

        class FakeSDK:
            models = _Models()

        class FakeClient:
            def _get_sdk_client(self):
                return FakeSDK()

        monkeypatch.setattr(groq_client, "GroqClient", lambda **kw: FakeClient())
        result = health.check_llm()
        assert result["status"] == "ok"
        assert result["provider"] == "groq"

    def test_groq_error(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "sk-dummy")

        import backend.groq_client as groq_client

        class FakeGroqError(Exception):
            pass

        class _Models:
            def list(self):
                raise FakeGroqError("rate limited")

        class FakeSDK:
            models = _Models()

        class FakeClient:
            def _get_sdk_client(self):
                return FakeSDK()

        monkeypatch.setattr(groq_client, "GroqClient", lambda **kw: FakeClient())
        monkeypatch.setattr(groq_client, "GroqError", FakeGroqError)
        result = health.check_llm()
        assert result["status"] == "error"
        assert result["provider"] == "groq"
        assert "rate limited" in result["error"]

    def test_generic_error(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "sk-dummy")

        import backend.groq_client as groq_client

        def _raise(_=None, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(groq_client, "GroqClient", _raise)
        result = health.check_llm()
        assert result["status"] == "error"
        assert "boom" in result["error"]


class TestCheckDisk:
    def test_ok(self):
        result = health.check_disk()
        assert result["status"] in ("ok", "warning", "critical")
        assert "free_gb" in result

    def test_disk_error(self, monkeypatch):
        def _boom(_):
            raise OSError("disk unavailable")

        monkeypatch.setattr(health.shutil, "disk_usage", _boom)
        result = health.check_disk()
        assert result["status"] == "error"


class TestDeepHealthCheck:
    def test_healthy(self, monkeypatch):
        monkeypatch.setattr(health, "check_database", lambda: {"status": "ok"})
        monkeypatch.setattr(health, "check_llm", lambda: {"status": "ok"})
        monkeypatch.setattr(health, "check_disk", lambda: {"status": "ok", "used_pct": 50})
        result = health.deep_health_check()
        assert result["status"] == "healthy"

    def test_degraded_on_llm_error(self, monkeypatch):
        monkeypatch.setattr(health, "check_database", lambda: {"status": "ok"})
        monkeypatch.setattr(health, "check_llm", lambda: {"status": "error"})
        monkeypatch.setattr(health, "check_disk", lambda: {"status": "ok"})
        result = health.deep_health_check()
        assert result["status"] == "degraded"

    def test_unhealthy_on_critical_disk(self, monkeypatch):
        monkeypatch.setattr(health, "check_database", lambda: {"status": "ok"})
        monkeypatch.setattr(health, "check_llm", lambda: {"status": "ok"})
        monkeypatch.setattr(health, "check_disk", lambda: {"status": "critical"})
        result = health.deep_health_check()
        assert result["status"] == "unhealthy"


class TestHealthEndpoint:
    def test_health_ok(self, monkeypatch):
        monkeypatch.setattr(health, "check_database", lambda: {"status": "ok"})
        monkeypatch.setattr(health, "check_llm", lambda: {"status": "ok"})
        monkeypatch.setattr(health, "check_disk", lambda: {"status": "ok", "used_pct": 50})
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert set(body["checks"]) == {"database", "llm", "disk"}

    def test_v1_health_deep(self, monkeypatch):
        monkeypatch.setattr(health, "check_database", lambda: {"status": "ok"})
        monkeypatch.setattr(health, "check_llm", lambda: {"status": "ok"})
        monkeypatch.setattr(health, "check_disk", lambda: {"status": "ok", "used_pct": 50})
        response = client.get("/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert set(body["checks"]) == {"database", "llm", "disk"}