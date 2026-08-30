"""
Prompt registry: versioned prompts loaded from JSON files.

Prompts are stored in prompts/ directory as versioned JSON files.
Each prompt has a name, version, and template content.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class PromptRegistry:
    """Load and serve versioned prompts from JSON files."""

    def __init__(self, prompts_dir: Optional[Path] = None):
        self._dir = prompts_dir or PROMPTS_DIR
        self._cache: dict[str, dict[str, Any]] = {}

    def _load_prompt(self, name: str) -> dict[str, Any]:
        if name in self._cache:
            return self._cache[name]
        path = self._dir / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"Prompt '{name}' not found at {path}")
        with open(path) as f:
            data = json.load(f)
        self._cache[name] = data
        return data

    def get(self, name: str, version: Optional[str] = None) -> str:
        """Get a prompt template by name and optional version."""
        data = self._load_prompt(name)
        versions = data.get("versions", {})
        if version is None:
            version = data.get("latest", "1.0")
        if version not in versions:
            raise ValueError(f"Prompt '{name}' version '{version}' not found")
        return versions[version]

    def list_prompts(self) -> list[dict[str, Any]]:
        """List all available prompts with metadata."""
        prompts = []
        if not self._dir.exists():
            return prompts
        for path in self._dir.glob("*.json"):
            with open(path) as f:
                data = json.load(f)
            prompts.append({
                "name": path.stem,
                "latest": data.get("latest", "1.0"),
                "versions": list(data.get("versions", {}).keys()),
                "description": data.get("description", ""),
            })
        return prompts


# Global registry instance
_registry: Optional[PromptRegistry] = None


def get_registry() -> PromptRegistry:
    global _registry
    if _registry is None:
        _registry = PromptRegistry()
    return _registry


def get_prompt(name: str, version: Optional[str] = None) -> str:
    """Convenience function to get a prompt template."""
    return get_registry().get(name, version)
