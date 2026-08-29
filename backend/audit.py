"""
Phase 9: Append-Only Audit Logger

Simple append-only audit trail in SQLite. One record per settlement per run.
Never update. Never delete. upload_hash groups records by batch.

Schema:
    CREATE TABLE audit_log (
        id TEXT PRIMARY KEY,
        upload_hash TEXT NOT NULL,
        settlement_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        decision_state TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from backend.models import DecisionState, ReconciliationResult, HumanReviewDecision


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    upload_hash TEXT NOT NULL,
    settlement_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    decision_state TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_audit_upload_hash ON audit_log (upload_hash);
"""

INDEX_SETTLEMENT_SQL = """
CREATE INDEX IF NOT EXISTS idx_audit_settlement_id ON audit_log (settlement_id);
"""


# ---------------------------------------------------------------------------
# Audit record
# ---------------------------------------------------------------------------

class AuditRecord:
    """A single append-only audit record."""

    def __init__(
        self,
        id: str,
        upload_hash: str,
        settlement_id: str,
        timestamp: str,
        decision_state: str,
        payload_json: str,
    ):
        self.id = id
        self.upload_hash = upload_hash
        self.settlement_id = settlement_id
        self.timestamp = timestamp
        self.decision_state = decision_state
        self.payload_json = payload_json

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "upload_hash": self.upload_hash,
            "settlement_id": self.settlement_id,
            "timestamp": self.timestamp,
            "decision_state": self.decision_state,
            "payload_json": self.payload_json,
        }

    def payload(self) -> dict[str, Any]:
        """Deserialize the JSON payload."""
        return json.loads(self.payload_json)


# ---------------------------------------------------------------------------
# Audit logger
# ---------------------------------------------------------------------------

DEFAULT_AUDIT_DIR = "data/audit"


class AuditLogger:
    """Append-only audit logger backed by SQLite.

    Usage:
        logger = AuditLogger("audit.db")
        logger.log_result("upload_hash_abc", result)
        records = logger.get_batch("upload_hash_abc")
    """

    def __init__(self, db_path: str = "data/audit/audit.db"):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(SCHEMA_SQL)
        self._conn.execute(INDEX_SQL)
        self._conn.execute(INDEX_SETTLEMENT_SQL)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def log_result(
        self,
        upload_hash: str,
        result: ReconciliationResult,
        extra_payload: Optional[dict[str, Any]] = None,
    ) -> AuditRecord:
        """Append a reconciliation result to the audit log.

        Args:
            upload_hash: Hash identifying the batch upload.
            result: ReconciliationResult from the engine.
            extra_payload: Optional additional data to include in the payload.

        Returns:
            The created AuditRecord.
        """
        record_id = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        payload: dict[str, Any] = {
            "settlement_id": result.settlement_id,
            "decision_state": result.decision.value,
            "difference_paise": result.difference_paise,
            "expected_amount_paise": result.expected_amount_paise,
            "actual_amount_paise": result.actual_amount_paise,
            "escalate_to_human": result.escalate_to_human,
            "deterministic_checks_passed": result.deterministic_checks_passed,
            "deterministic_checks_failed": result.deterministic_checks_failed,
        }

        if result.ai_response is not None:
            payload["ai_response"] = {
                "classification": result.ai_response.classification.value,
                "explanation": result.ai_response.explanation,
                "raw_confidence": result.ai_response.raw_confidence,
                "cited_evidence": result.ai_response.cited_evidence,
                "recommended_action": result.ai_response.recommended_action,
            }

        if extra_payload:
            payload["extra"] = extra_payload

        payload_json = json.dumps(payload, default=str)

        record = AuditRecord(
            id=record_id,
            upload_hash=upload_hash,
            settlement_id=result.settlement_id,
            timestamp=timestamp,
            decision_state=result.decision.value,
            payload_json=payload_json,
        )

        self._conn.execute(
            "INSERT INTO audit_log (id, upload_hash, settlement_id, timestamp, decision_state, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (record.id, record.upload_hash, record.settlement_id,
             record.timestamp, record.decision_state, record.payload_json),
        )
        self._conn.commit()

        return record

    def log_batch(
        self,
        upload_hash: str,
        results: list[ReconciliationResult],
    ) -> list[AuditRecord]:
        """Log an entire batch of reconciliation results.

        Args:
            upload_hash: Hash identifying the batch upload.
            results: List of ReconciliationResult from the engine.

        Returns:
            List of created AuditRecords.
        """
        records = []
        for result in results:
            record = self.log_result(upload_hash, result)
            records.append(record)
        return records

    def get_batch(self, upload_hash: str) -> list[AuditRecord]:
        """Retrieve all audit records for a given upload hash.

        Args:
            upload_hash: The batch upload hash.

        Returns:
            List of AuditRecords in insertion order.
        """
        cursor = self._conn.execute(
            "SELECT id, upload_hash, settlement_id, timestamp, decision_state, payload_json "
            "FROM audit_log WHERE upload_hash = ? ORDER BY timestamp",
            (upload_hash,),
        )
        rows = cursor.fetchall()
        return [
            AuditRecord(
                id=row[0],
                upload_hash=row[1],
                settlement_id=row[2],
                timestamp=row[3],
                decision_state=row[4],
                payload_json=row[5],
            )
            for row in rows
        ]

    def get_settlement_history(self, settlement_id: str) -> list[AuditRecord]:
        """Retrieve all audit records for a specific settlement across batches.

        Args:
            settlement_id: The settlement ID to look up.

        Returns:
            List of AuditRecords in insertion order.
        """
        cursor = self._conn.execute(
            "SELECT id, upload_hash, settlement_id, timestamp, decision_state, payload_json "
            "FROM audit_log WHERE settlement_id = ? ORDER BY timestamp",
            (settlement_id,),
        )
        rows = cursor.fetchall()
        return [
            AuditRecord(
                id=row[0],
                upload_hash=row[1],
                settlement_id=row[2],
                timestamp=row[3],
                decision_state=row[4],
                payload_json=row[5],
            )
            for row in rows
        ]

    def count_by_decision(self, upload_hash: str) -> dict[str, int]:
        """Count records by decision state for a given batch.

        Args:
            upload_hash: The batch upload hash.

        Returns:
            Dict mapping decision state to count.
        """
        cursor = self._conn.execute(
            "SELECT decision_state, COUNT(*) FROM audit_log WHERE upload_hash = ? GROUP BY decision_state",
            (upload_hash,),
        )
        return dict(cursor.fetchall())

    def total_records(self, upload_hash: str) -> int:
        """Count total audit records for a given batch.

        Args:
            upload_hash: The batch upload hash.

        Returns:
            Total record count.
        """
        cursor = self._conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE upload_hash = ?",
            (upload_hash,),
        )
        return cursor.fetchone()[0]

    def log_human_review(
        self,
        settlement_id: str,
        review: HumanReviewDecision,
    ) -> AuditRecord:
        """Log a human review decision as an audit record.

        Uses upload_hash = 'human_review' to distinguish from batch uploads.
        """
        record_id = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        payload: dict[str, Any] = {
            "settlement_id": review.settlement_id,
            "decision_state": "RESOLVED_BY_HUMAN" if review.decision != "REJECT" else "REJECTED",
            "human_review": {
                "decision": review.decision,
                "reason": review.reason,
                "reviewer_id": review.reviewer_id,
                "timestamp": review.timestamp.isoformat(),
            },
        }

        payload_json = json.dumps(payload, default=str)

        record = AuditRecord(
            id=record_id,
            upload_hash="human_review",
            settlement_id=settlement_id,
            timestamp=timestamp,
            decision_state=payload["decision_state"],
            payload_json=payload_json,
        )

        self._conn.execute(
            "INSERT INTO audit_log (id, upload_hash, settlement_id, timestamp, decision_state, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (record.id, record.upload_hash, record.settlement_id,
             record.timestamp, record.decision_state, record.payload_json),
        )
        self._conn.commit()

        return record


# ---------------------------------------------------------------------------
# Convenience: in-memory audit (for testing)
# ---------------------------------------------------------------------------

class InMemoryAuditLogger:
    """In-memory audit logger for testing. No SQLite dependency."""

    def __init__(self):
        self.records: list[AuditRecord] = []

    def close(self) -> None:
        pass

    def log_result(
        self,
        upload_hash: str,
        result: ReconciliationResult,
        extra_payload: Optional[dict[str, Any]] = None,
    ) -> AuditRecord:
        record_id = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        payload: dict[str, Any] = {
            "settlement_id": result.settlement_id,
            "decision_state": result.decision.value,
            "difference_paise": result.difference_paise,
            "expected_amount_paise": result.expected_amount_paise,
            "actual_amount_paise": result.actual_amount_paise,
            "escalate_to_human": result.escalate_to_human,
            "deterministic_checks_passed": result.deterministic_checks_passed,
            "deterministic_checks_failed": result.deterministic_checks_failed,
        }

        if result.ai_response is not None:
            payload["ai_response"] = {
                "classification": result.ai_response.classification.value,
                "explanation": result.ai_response.explanation,
                "raw_confidence": result.ai_response.raw_confidence,
                "cited_evidence": result.ai_response.cited_evidence,
                "recommended_action": result.ai_response.recommended_action,
            }

        if extra_payload:
            payload["extra"] = extra_payload

        payload_json = json.dumps(payload, default=str)

        record = AuditRecord(
            id=record_id,
            upload_hash=upload_hash,
            settlement_id=result.settlement_id,
            timestamp=timestamp,
            decision_state=result.decision.value,
            payload_json=payload_json,
        )

        self.records.append(record)
        return record

    def log_batch(
        self,
        upload_hash: str,
        results: list[ReconciliationResult],
    ) -> list[AuditRecord]:
        return [self.log_result(upload_hash, r) for r in results]

    def get_batch(self, upload_hash: str) -> list[AuditRecord]:
        return [r for r in self.records if r.upload_hash == upload_hash]

    def get_settlement_history(self, settlement_id: str) -> list[AuditRecord]:
        return [r for r in self.records if r.settlement_id == settlement_id]

    def count_by_decision(self, upload_hash: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.records:
            if r.upload_hash == upload_hash:
                counts[r.decision_state] = counts.get(r.decision_state, 0) + 1
        return counts

    def total_records(self, upload_hash: str) -> int:
        return len(self.get_batch(upload_hash))

    def log_human_review(
        self,
        settlement_id: str,
        review: HumanReviewDecision,
    ) -> AuditRecord:
        """Log a human review decision (in-memory version)."""
        record_id = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        payload: dict[str, Any] = {
            "settlement_id": review.settlement_id,
            "decision_state": "RESOLVED_BY_HUMAN" if review.decision != "REJECT" else "REJECTED",
            "human_review": {
                "decision": review.decision,
                "reason": review.reason,
                "reviewer_id": review.reviewer_id,
                "timestamp": review.timestamp.isoformat(),
            },
        }

        payload_json = json.dumps(payload, default=str)

        record = AuditRecord(
            id=record_id,
            upload_hash="human_review",
            settlement_id=settlement_id,
            timestamp=timestamp,
            decision_state=payload["decision_state"],
            payload_json=payload_json,
        )

        self.records.append(record)
        return record
