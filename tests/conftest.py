import os
import pytest


@pytest.fixture(autouse=True)
def clean_audit_db():
    """Remove persistent audit DB before each test to ensure clean state."""
    db_path = os.path.join(os.path.dirname(__file__), "..", "data", "audit", "audit.db")
    if os.path.exists(db_path):
        os.remove(db_path)
