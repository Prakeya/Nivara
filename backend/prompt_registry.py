"""
Prompt registry: versioned prompts loaded from JSON and Markdown files.

Prompts are stored in prompts/ directory as versioned files:
- prompts/reconciliation_system.json (legacy format)
- prompts/v1/system.md (new format)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class PromptRegistry:
    """Load and serve versioned prompts from JSON and MD files."""

    def __init__(self, prompts_dir: Optional[Path] = None):
        self._dir = prompts_dir or PROMPTS_DIR
        self._cache: dict[str, dict[str, Any]] = {}

    def _load_json_prompt(self, name: str) -> dict[str, Any]:
        path = self._dir / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"Prompt '{name}' not found at {path}")
        with open(path) as f:
            data = json.load(f)
        return data  # type: ignore[no-any-return]

    def _load_md_prompt(self, version: str, name: str = "system") -> dict[str, Any]:
        path = self._dir / version / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"Prompt '{version}/{name}' not found at {path}")
        with open(path) as f:
            content = f.read()
        return {
            "description": f"{name} prompt for {version}",
            "latest": version,
            "versions": {version: content},
        }

    def _load_prompt(self, name: str) -> dict[str, Any]:
        if name in self._cache:
            return self._cache[name]

        # Try JSON format first
        json_path = self._dir / f"{name}.json"
        if json_path.exists():
            data = self._load_json_prompt(name)
            self._cache[name] = data
            return data

        # Try v{N}/system.md format (e.g., "v1/system")
        parts = name.split("/")
        if len(parts) == 2 and parts[1] == "system":
            version = parts[0]  # e.g., "v1"
            data = self._load_md_prompt(version, "system")
            self._cache[name] = data
            return data

        raise FileNotFoundError(f"Prompt '{name}' not found")

    def get(self, name: str, version: Optional[str] = None) -> str:
        """Get a prompt template by name and optional version."""
        data = self._load_prompt(name)
        versions = data.get("versions", {})
        if version is None:
            version = data.get("latest", "1.0")
        if version not in versions:
            raise ValueError(f"Prompt '{name}' version '{version}' not found")
        return versions[version]  # type: ignore[no-any-return]

    def get_version(self, name: str) -> str:
        """Get the latest version string for a prompt."""
        data = self._load_prompt(name)
        return data.get("latest", "1.0")  # type: ignore[no-any-return]

    def list_prompts(self) -> list[dict[str, Any]]:
        """List all available prompts with metadata."""
        prompts: list[dict[str, Any]] = []
        if not self._dir.exists():
            return prompts

        # JSON prompts
        for path in self._dir.glob("*.json"):
            with open(path) as f:
                data = json.load(f)
            prompts.append({
                "name": path.stem,
                "latest": data.get("latest", "1.0"),
                "versions": list(data.get("versions", {}).keys()),
                "description": data.get("description", ""),
                "format": "json",
            })

        # Markdown prompts (v{N}/system.md)
        for version_dir in self._dir.glob("v*"):
            if version_dir.is_dir():
                md_file = version_dir / "system.md"
                if md_file.exists():
                    with open(md_file) as f:
                        content = f.read()
                    prompts.append({
                        "name": f"{version_dir.name}/system",
                        "latest": version_dir.name,
                        "versions": [version_dir.name],
                        "description": f"System prompt for {version_dir.name}",
                        "format": "markdown",
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


def get_prompt_version(name: str) -> str:
    """Get the latest version string for a prompt."""
    return get_registry().get_version(name)
