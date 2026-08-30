import json
import json
import os
import pathlib
import pytest


@pytest.fixture(autouse=True)
def clean_audit_db():
    """Remove persistent audit DB and its WAL/SHM files before each test."""
    db_dir = pathlib.Path(__file__).resolve().parent.parent / "data" / "audit"
    for suffix in ("", "-wal", "-shm"):
        p = db_dir / f"audit.db{suffix}"
        p.unlink(missing_ok=True)


@pytest.fixture
def tmp_prompts_dir(tmp_path):
    """Create a temporary prompts directory with test fixtures."""
    # JSON prompt
    json_prompt = {
        "description": "Test prompt",
        "latest": "1.0",
        "versions": {
            "1.0": "Test prompt v1 content",
            "2.0": "Test prompt v2 content",
        },
    }
    (tmp_path / "test_prompt.json").write_text(json.dumps(json_prompt))

    # Markdown prompt
    v1_dir = tmp_path / "v1"
    v1_dir.mkdir()
    (v1_dir / "system.md").write_text("# System Prompt\n\nSettlement reconciliation analyst.\n")

    return tmp_path
