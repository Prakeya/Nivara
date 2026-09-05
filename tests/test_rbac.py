"""
Unit tests for RBAC (Role-Based Access Control).

Covers: role-map loading from env vars, permission matrix, the
RBACDependency FastAPI dependency, and the documented "no NIVARA_API_KEY
means every request is treated as ADMIN" behavior (Task 5).
"""

import asyncio

import pytest
from fastapi import HTTPException

from backend import rbac as rbac_module
from backend.rbac import (
    Role,
    RBACDependency,
    check_permission,
    get_role_from_api_key,
    get_role_map,
)


@pytest.fixture(autouse=True)
def _reset_role_map(monkeypatch):
    """Clear the cached role map before/after each test so env changes take effect."""
    monkeypatch.setattr(rbac_module, "_role_map", None)
    yield
    monkeypatch.setattr(rbac_module, "_role_map", None)


class _FakeRequest:
    """Minimal stand-in for fastapi.Request exposing only .headers."""

    def __init__(self, headers=None):
        self.headers = headers or {}


class TestPermissionMatrix:
    def test_admin_has_all_permissions(self):
        for perm in ("upload", "review", "read", "configure", "delete"):
            assert check_permission(Role.ADMIN, perm) is True

    def test_reviewer_has_review_and_read(self):
        assert check_permission(Role.REVIEWER, "review") is True
        assert check_permission(Role.REVIEWER, "read") is True
        assert check_permission(Role.REVIEWER, "upload") is False
        assert check_permission(Role.REVIEWER, "configure") is False

    def test_viewer_has_only_read(self):
        assert check_permission(Role.VIEWER, "read") is True
        assert check_permission(Role.VIEWER, "review") is False
        assert check_permission(Role.VIEWER, "upload") is False

    def test_unknown_role_has_no_permissions(self):
        # check_permission defaults to an empty set for roles not in PERMISSIONS
        assert check_permission(Role.VIEWER, "delete") is False


class TestRoleMapLoading:
    def test_load_role_map_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("NIVARA_ROLE_mykey", "admin")
        role_map = get_role_map()
        assert role_map["mykey"] == Role.ADMIN

    def test_load_role_map_ignores_invalid_role(self, monkeypatch, caplog):
        monkeypatch.setenv("NIVARA_ROLE_badkey", "superuser")
        role_map = get_role_map()
        assert "badkey" not in role_map

    def test_load_role_map_caches_result(self, monkeypatch):
        monkeypatch.setenv("NIVARA_ROLE_cachekey", "reviewer")
        first = get_role_map()
        # Mutating env after first load should not affect the cached map
        monkeypatch.delenv("NIVARA_ROLE_cachekey", raising=False)
        second = get_role_map()
        assert first is second
        assert "cachekey" in second

    def test_get_role_from_api_key_defaults_to_viewer(self, monkeypatch):
        monkeypatch.setenv("NIVARA_ROLE_known", "admin")
        assert get_role_from_api_key("unknown-key") == Role.VIEWER
        assert get_role_from_api_key("known") == Role.ADMIN


class TestRBACDependencyNoApiKeyConfigured:
    """Task 5 documented: when NIVARA_API_KEY is unset, every request is ADMIN."""

    def test_no_api_key_env_means_admin(self, monkeypatch):
        monkeypatch.delenv("NIVARA_API_KEY", raising=False)
        dependency = RBACDependency("delete")
        request = _FakeRequest(headers={})  # no X-API-Key header either

        role = asyncio.run(dependency(request))

        assert role == Role.ADMIN

    def test_no_api_key_env_grants_configure_even_without_header(self, monkeypatch):
        monkeypatch.delenv("NIVARA_API_KEY", raising=False)
        dependency = RBACDependency("configure")
        request = _FakeRequest(headers={"X-API-Key": "anything-or-nothing"})

        role = asyncio.run(dependency(request))

        assert role == Role.ADMIN


class TestRBACDependencyWithApiKeyConfigured:
    def test_valid_admin_key_passes_configure_check(self, monkeypatch):
        monkeypatch.setenv("NIVARA_API_KEY", "server-secret")
        monkeypatch.setenv("NIVARA_ROLE_adminkey", "admin")
        dependency = RBACDependency("configure")
        request = _FakeRequest(headers={"X-API-Key": "adminkey"})

        role = asyncio.run(dependency(request))

        assert role == Role.ADMIN

    def test_viewer_key_denied_for_upload(self, monkeypatch):
        monkeypatch.setenv("NIVARA_API_KEY", "server-secret")
        monkeypatch.setenv("NIVARA_ROLE_viewerkey", "viewer")
        dependency = RBACDependency("upload")
        request = _FakeRequest(headers={"X-API-Key": "viewerkey"})

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(dependency(request))

        assert exc_info.value.status_code == 403
        assert "Insufficient permissions" in exc_info.value.detail

    def test_missing_header_defaults_to_viewer_and_is_denied_for_review(self, monkeypatch):
        monkeypatch.setenv("NIVARA_API_KEY", "server-secret")
        dependency = RBACDependency("review")
        request = _FakeRequest(headers={})

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(dependency(request))

        assert exc_info.value.status_code == 403
        assert "Got: viewer" in exc_info.value.detail

    def test_missing_header_allows_read(self, monkeypatch):
        monkeypatch.setenv("NIVARA_API_KEY", "server-secret")
        dependency = RBACDependency("read")
        request = _FakeRequest(headers={})

        role = asyncio.run(dependency(request))

        assert role == Role.VIEWER
