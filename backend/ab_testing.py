"""
Model A/B testing framework: routes settlements to different LLM models
based on traffic split configuration.

Configure via NIVARA_AB_CONFIG env var or config/ab_testing.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from pathlib import Path
from typing import Any, Optional


class ABTestConfig:
    """A/B test configuration for LLM models."""

    def __init__(self, config_path: Optional[Path] = None):
        self._path = config_path or Path(__file__).parent.parent / "config" / "ab_testing.json"
        self._config: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            with open(self._path) as f:
                self._config = json.load(f)
        else:
            self._config = {
                "enabled": False,
                "default_model": "gpt-4o-mini",
                "variants": {},
            }

    @property
    def enabled(self) -> bool:
        return self._config.get("enabled", False)  # type: ignore[no-any-return]

    @property
    def default_model(self) -> str:
        return self._config.get("default_model", "gpt-4o-mini")  # type: ignore[no-any-return]

    def get_model_for_settlement(self, settlement_id: str) -> str:
        """Deterministically assign a model based on settlement_id hash."""
        if not self.enabled:
            return self.default_model

        variants = self._config.get("variants", {})
        if not variants:
            return self.default_model

        # Deterministic hash-based routing
        h = int(hashlib.md5(settlement_id.encode(), usedforsecurity=False).hexdigest()[:8], 16)
        roll = (h % 10000) / 10000.0  # 0.0 to 1.0

        cumulative = 0.0
        for variant_name, variant_config in variants.items():
            cumulative += variant_config.get("traffic_pct", 0)
            if roll < cumulative:
                return variant_config.get("model", self.default_model)  # type: ignore[no-any-return]

        return self.default_model

    def record_result(self, settlement_id: str, model: str, metrics: dict[str, Any]) -> None:
        """Record A/B test results (latency, cost, accuracy)."""
        # In production, write to metrics store
        pass


# Global instance
_ab_config: Optional[ABTestConfig] = None


def get_ab_config() -> ABTestConfig:
    global _ab_config
    if _ab_config is None:
        _ab_config = ABTestConfig()
    return _ab_config
