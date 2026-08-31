"""
Database abstraction layer supporting SQLite (default) and PostgreSQL.

Set NIVARA_DATABASE_URL to a PostgreSQL connection string to switch:
  NIVARA_DATABASE_URL=postgresql://user:pass@localhost:5432/nivara

Falls back to SQLite when DATABASE_URL is not set.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Generator, Optional

DATABASE_URL = os.environ.get("NIVARA_DATABASE_URL", "")


def is_postgres() -> bool:
    return DATABASE_URL.startswith("postgresql")


@contextmanager
def get_connection() -> Generator[Any, None, None]:
    """Yield a database connection. Auto-commits on success, rolls back on error."""
    if is_postgres():
        import psycopg2  # type: ignore[import-untyped]
        conn = psycopg2.connect(DATABASE_URL)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect("data/audit/audit.db", timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db() -> None:
    """Create tables if they don't exist."""
    with get_connection() as conn:
        if is_postgres():
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id TEXT PRIMARY KEY,
                    upload_hash TEXT NOT NULL,
                    settlement_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    decision_state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    record_hash TEXT NOT NULL,
                    prev_hash TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_upload_hash ON audit_log (upload_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_settlement_id ON audit_log (settlement_id)")
        else:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id TEXT PRIMARY KEY,
                    upload_hash TEXT NOT NULL,
                    settlement_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    decision_state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    record_hash TEXT NOT NULL,
                    prev_hash TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_upload_hash ON audit_log (upload_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_settlement_id ON audit_log (settlement_id)")


def execute_query(sql: str, params: tuple[str, ...] = (), fetch: bool = False) -> list[tuple[Any, ...]]:
    """Execute a query. Returns rows if fetch=True."""
    with get_connection() as conn:
        cursor = conn.execute(sql, params)
        if fetch:
            return cursor.fetchall()  # type: ignore[no-any-return]
        return []


def execute_many(sql: str, params_list: list[tuple[str, ...]]) -> None:
    """Execute many inserts in a single transaction."""
    with get_connection() as conn:
        conn.executemany(sql, params_list)
