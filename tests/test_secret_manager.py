"""
Unit tests for the secret manager (env / AWS / Vault backends).
"""

from unittest.mock import MagicMock, patch

import pytest

from backend import secret_manager


@pytest.fixture(autouse=True)
def _clear_cache_and_backend(monkeypatch):
    """Isolate each test: reset the secret cache and default to the env backend."""
    secret_manager.clear_cache()
    monkeypatch.setattr(secret_manager, "SECRET_BACKEND", "env")
    yield
    secret_manager.clear_cache()


class TestGetSecretEnvBackend:
    def test_reads_from_environment(self, monkeypatch):
        monkeypatch.setenv("MY_SECRET", "shh")
        assert secret_manager.get_secret("MY_SECRET") == "shh"

    def test_returns_default_when_missing(self, monkeypatch):
        monkeypatch.delenv("MISSING_SECRET", raising=False)
        assert secret_manager.get_secret("MISSING_SECRET", default="fallback") == "fallback"

    def test_returns_none_when_missing_and_no_default(self, monkeypatch):
        monkeypatch.delenv("MISSING_SECRET", raising=False)
        assert secret_manager.get_secret("MISSING_SECRET") is None

    def test_caches_value_after_first_lookup(self, monkeypatch):
        monkeypatch.setenv("CACHED_SECRET", "v1")
        first = secret_manager.get_secret("CACHED_SECRET")
        monkeypatch.setenv("CACHED_SECRET", "v2")
        second = secret_manager.get_secret("CACHED_SECRET")
        assert first == "v1"
        assert second == "v1"  # cache wins, env change not observed

    def test_clear_cache_forces_reread(self, monkeypatch):
        monkeypatch.setenv("CACHED_SECRET", "v1")
        secret_manager.get_secret("CACHED_SECRET")
        monkeypatch.setenv("CACHED_SECRET", "v2")
        secret_manager.clear_cache()
        assert secret_manager.get_secret("CACHED_SECRET") == "v2"


class TestGetSecretAwsBackend:
    def test_aws_backend_returns_json_value_field(self, monkeypatch):
        monkeypatch.setattr(secret_manager, "SECRET_BACKEND", "aws")
        fake_boto3 = MagicMock()
        fake_client = MagicMock()
        fake_client.get_secret_value.return_value = {
            "SecretString": '{"value": "aws-secret-value"}'
        }
        fake_boto3.client.return_value = fake_client

        with patch.dict("sys.modules", {"boto3": fake_boto3}):
            result = secret_manager.get_secret("db-password")

        assert result == "aws-secret-value"
        fake_client.get_secret_value.assert_called_once_with(SecretId="db-password")

    def test_aws_backend_returns_raw_string_when_not_json(self, monkeypatch):
        monkeypatch.setattr(secret_manager, "SECRET_BACKEND", "aws")
        fake_boto3 = MagicMock()
        fake_client = MagicMock()
        fake_client.get_secret_value.return_value = {"SecretString": "plain-text-secret"}
        fake_boto3.client.return_value = fake_client

        with patch.dict("sys.modules", {"boto3": fake_boto3}):
            result = secret_manager.get_secret("db-password")

        assert result == "plain-text-secret"

    def test_aws_backend_returns_none_on_exception(self, monkeypatch):
        monkeypatch.setattr(secret_manager, "SECRET_BACKEND", "aws")
        fake_boto3 = MagicMock()
        fake_boto3.client.side_effect = RuntimeError("no credentials")

        with patch.dict("sys.modules", {"boto3": fake_boto3}):
            result = secret_manager.get_secret("db-password")

        assert result is None


class TestGetSecretVaultBackend:
    def test_vault_backend_returns_value_on_200(self, monkeypatch):
        monkeypatch.setattr(secret_manager, "SECRET_BACKEND", "vault")
        monkeypatch.setenv("VAULT_ADDR", "http://vault.local:8200")
        monkeypatch.setenv("VAULT_TOKEN", "vault-token")

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {"data": {"data": {"value": "vault-secret"}}}

        fake_httpx = MagicMock()
        fake_httpx.get.return_value = fake_response

        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            result = secret_manager.get_secret("api-key")

        assert result == "vault-secret"
        fake_httpx.get.assert_called_once()
        call_kwargs = fake_httpx.get.call_args
        assert "vault.local:8200" in call_kwargs.args[0]

    def test_vault_backend_returns_none_on_non_200(self, monkeypatch):
        monkeypatch.setattr(secret_manager, "SECRET_BACKEND", "vault")
        fake_response = MagicMock()
        fake_response.status_code = 404

        fake_httpx = MagicMock()
        fake_httpx.get.return_value = fake_response

        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            result = secret_manager.get_secret("missing-key")

        assert result is None

    def test_vault_backend_returns_none_on_exception(self, monkeypatch):
        monkeypatch.setattr(secret_manager, "SECRET_BACKEND", "vault")
        fake_httpx = MagicMock()
        fake_httpx.get.side_effect = RuntimeError("connection refused")

        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            result = secret_manager.get_secret("api-key")

        assert result is None


class TestClearCache:
    def test_clear_cache_empties_internal_cache(self, monkeypatch):
        monkeypatch.setenv("SOME_SECRET", "value")
        secret_manager.get_secret("SOME_SECRET")
        assert "SOME_SECRET" in secret_manager._secret_cache
        secret_manager.clear_cache()
        assert secret_manager._secret_cache == {}
