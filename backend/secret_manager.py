"""
Secret manager integration: supports environment variables (default) and
AWS Secrets Manager / HashiCorp Vault when configured.

Set NIVARA_SECRET_BACKEND to switch:
  - "env" (default): reads from environment variables
  - "aws": reads from AWS Secrets Manager
  - "vault": reads from HashiCorp Vault
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("nivara.secret_manager")

SECRET_BACKEND = os.environ.get("NIVARA_SECRET_BACKEND", "env")
_secret_cache: dict[str, str] = {}


def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    """Get a secret value by name."""
    if name in _secret_cache:
        return _secret_cache[name]

    value = None
    if SECRET_BACKEND == "aws":
        value = _get_from_aws(name)
    elif SECRET_BACKEND == "vault":
        value = _get_from_vault(name)
    else:
        value = os.environ.get(name, default)

    if value is not None:
        _secret_cache[name] = value
    return value


def _get_from_aws(name: str) -> Optional[str]:
    """Retrieve secret from AWS Secrets Manager."""
    try:
        import boto3
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=name)
        secret = response.get("SecretString", "")
        # Try JSON parse
        try:
            data = json.loads(secret)
            return data.get("value", secret)
        except json.JSONDecodeError:
            return secret
    except Exception:
        logger.exception("Failed to retrieve secret '%s' from AWS Secrets Manager", name)
        return None


def _get_from_vault(name: str) -> Optional[str]:
    """Retrieve secret from HashiCorp Vault."""
    try:
        import httpx
        vault_url = os.environ.get("VAULT_ADDR", "http://localhost:8200")
        vault_token = os.environ.get("VAULT_TOKEN", "")
        path = os.environ.get("VAULT_SECRET_PATH", "secret/data/nivara")

        resp = httpx.get(
            f"{vault_url}/v1/{path}/{name}",
            headers={"X-Vault-Token": vault_token},
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("data", {}).get("data", {}).get("value")
    except Exception:
        logger.exception("Failed to retrieve secret '%s' from HashiCorp Vault", name)
        return None
    return None


def clear_cache() -> None:
    """Clear the secret cache."""
    _secret_cache.clear()
