"""
Configuration loader: reads fee/tax structure from JSON config files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

CONFIG_DIR = Path(__file__).parent.parent / "config"


class ConfigLoader:
    """Load and cache configuration from JSON files."""

    def __init__(self, config_dir: Optional[Path] = None):
        self._dir = config_dir or CONFIG_DIR
        self._cache: dict[str, Any] = {}

    def _load(self, name: str) -> dict[str, Any]:
        if name in self._cache:
            return self._cache[name]  # type: ignore[no-any-return]
        path = self._dir / f"{name}.json"
        if not path.exists():
            return {}
        with open(path) as f:
            data = json.load(f)
        self._cache[name] = data
        return data  # type: ignore[no-any-return]

    def get_fee_structure(self, method: str) -> dict[str, Any]:
        """Get fee structure for a payment method."""
        config = self._load("fee_structure")
        structures = config.get("fee_structures", {})
        return structures.get(method, structures.get("unknown", {  # type: ignore[no-any-return]
            "percentage": 0, "fixed_paise": 0, "gst_percentage": 18
        }))

    def get_settlement_cycle(self, cycle: str) -> dict[str, Any]:
        """Get settlement cycle config."""
        config = self._load("fee_structure")
        return config.get("settlement_cycles", {}).get(cycle, {"expected_days": 2})  # type: ignore[no-any-return]

    def get_tolerance(self, check_type: str) -> int:
        """Get tolerance in paise for a check type."""
        config = self._load("fee_structure")
        tolerances = config.get("tolerance_paise", {})
        return tolerances.get(check_type, 0)  # type: ignore[no-any-return]

    def reload(self) -> None:
        """Clear cache and reload all configs."""
        self._cache.clear()


# Global instance
_config: Optional[ConfigLoader] = None


def get_config() -> ConfigLoader:
    global _config
    if _config is None:
        _config = ConfigLoader()
    return _config
