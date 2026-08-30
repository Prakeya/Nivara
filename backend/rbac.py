"""
Role-Based Access Control (RBAC): admin, reviewer, viewer.

Roles:
- admin: Full access (upload, review, configure)
- reviewer: Can submit review decisions
- viewer: Read-only access (status, audit, results)
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Optional

from fastapi import HTTPException, Request


class Role(Enum):
    ADMIN = "admin"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


# Permission matrix
PERMISSIONS = {
    Role.ADMIN: {"upload", "review", "read", "configure", "delete"},
    Role.REVIEWER: {"review", "read"},
    Role.VIEWER: {"read"},
}


# API key → role mapping (from env: NIVARA_ROLE_<key>=role)
def _load_role_map() -> dict[str, Role]:
    """Load API key → role mapping from environment variables."""
    mapping = {}
    for key, value in os.environ.items():
        if key.startswith("NIVARA_ROLE_"):
            api_key = key[len("NIVARA_ROLE_"):]
            try:
                role = Role(value.lower())
                mapping[api_key] = role
            except ValueError:
                pass
    return mapping


_role_map: Optional[dict[str, Role]] = None


def get_role_map() -> dict[str, Role]:
    global _role_map
    if _role_map is None:
        _role_map = _load_role_map()
    return _role_map


def get_role_from_api_key(api_key: str) -> Role:
    """Get role for an API key. Defaults to VIEWER if not found."""
    role_map = get_role_map()
    return role_map.get(api_key, Role.VIEWER)


def check_permission(role: Role, permission: str) -> bool:
    """Check if a role has a specific permission."""
    return permission in PERMISSIONS.get(role, set())


class RBACDependency:
    """FastAPI dependency for role-based access control."""

    def __init__(self, required_permission: str):
        self.required_permission = required_permission

    async def __call__(self, request: Request) -> Role:
        api_key = request.headers.get("X-API-Key", "")
        role = get_role_from_api_key(api_key)

        if not check_permission(role, self.required_permission):
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions. Required: {self.required_permission}, Got: {role.value}",
            )
        return role


# Convenience dependencies
require_upload = RBACDependency("upload")
require_review = RBACDependency("review")
require_read = RBACDependency("read")
require_configure = RBACDependency("configure")
