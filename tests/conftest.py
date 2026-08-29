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
