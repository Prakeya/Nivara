"""
Tests for Prompt Registry — JSON and Markdown prompt loading.

Covers:
- JSON prompt loading
- Markdown prompt loading (v1/system.md)
- Version retrieval
- List prompts
- Error handling
"""

from __future__ import annotations

import pytest

from backend.prompt_registry import PromptRegistry, get_prompt, get_prompt_version, get_registry
from tests.conftest import tmp_prompts_dir


class TestPromptRegistryJSON:
    def test_load_json_prompt(self, tmp_prompts_dir) -> None:
        registry = PromptRegistry(prompts_dir=tmp_prompts_dir)
        prompt = registry.get("test_prompt")
        assert "Test prompt" in prompt

    def test_load_json_prompt_specific_version(self, tmp_prompts_dir) -> None:
        registry = PromptRegistry(prompts_dir=tmp_prompts_dir)
        prompt = registry.get("test_prompt", version="1.0")
        assert "v1 content" in prompt

    def test_json_prompt_not_found(self, tmp_prompts_dir) -> None:
        registry = PromptRegistry(prompts_dir=tmp_prompts_dir)
        with pytest.raises(FileNotFoundError):
            registry.get("nonexistent")


class TestPromptRegistryMarkdown:
    def test_load_md_prompt(self, tmp_prompts_dir) -> None:
        registry = PromptRegistry(prompts_dir=tmp_prompts_dir)
        prompt = registry.get("v1/system")
        assert "settlement reconciliation" in prompt.lower()

    def test_md_prompt_version(self, tmp_prompts_dir) -> None:
        registry = PromptRegistry(prompts_dir=tmp_prompts_dir)
        version = registry.get_version("v1/system")
        assert version == "v1"

    def test_md_prompt_not_found(self, tmp_prompts_dir) -> None:
        registry = PromptRegistry(prompts_dir=tmp_prompts_dir)
        with pytest.raises(FileNotFoundError):
            registry.get("v99/system")


class TestPromptRegistryList:
    def test_list_prompts(self, tmp_prompts_dir) -> None:
        registry = PromptRegistry(prompts_dir=tmp_prompts_dir)
        prompts = registry.list_prompts()
        names = [p["name"] for p in prompts]
        assert "test_prompt" in names
        assert "v1/system" in names

    def test_list_prompts_format(self, tmp_prompts_dir) -> None:
        registry = PromptRegistry(prompts_dir=tmp_prompts_dir)
        prompts = registry.list_prompts()
        json_prompt = next(p for p in prompts if p["name"] == "test_prompt")
        md_prompt = next(p for p in prompts if p["name"] == "v1/system")
        assert json_prompt["format"] == "json"
        assert md_prompt["format"] == "markdown"


class TestPromptRegistryGlobal:
    def test_get_prompt_global(self) -> None:
        prompt = get_prompt("reconciliation_system")
        assert len(prompt) > 0

    def test_get_prompt_version_global(self) -> None:
        version = get_prompt_version("reconciliation_system")
        assert version == "1.0"

    def test_registry_singleton(self) -> None:
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2
