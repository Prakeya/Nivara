"""
Phase 9: Append-Only Audit Logger

Simple append-only audit trail in SQLite with SHA-256 hash chaining.
One record per settlement per run. Never update. Never delete.
upload_hash groups records by batch.

Each record includes a SHA-256 hash of its payload plus the previous
record's hash, creating a tamper-evident chain. Modifying any record
invalidates all subsequent hashes.

Schema:
    CREATE TABLE audit_log (
        id TEXT PRIMARY KEY,
        upload_hash TEXT NOT NULL,
        settlement_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        decision_state TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        record_hash TEXT NOT NULL,
        prev_hash TEXT
    );
"""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from backend.models import DecisionState, ReconciliationResult, HumanReviewDecision
from backend.pii_redaction import redact_dict

logger = logging.getLogger(__name__)


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
    payload_json TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    prev_hash TEXT
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_audit_upload_hash ON audit_log (upload_hash);
"""

INDEX_SETTLEMENT_SQL = """
CREATE INDEX IF NOT EXISTS idx_audit_settlement_id ON audit_log (settlement_id);
"""


def _compute_record_hash(payload_json: str, prev_hash: Optional[str] = None) -> str:
    """Compute SHA-256 hash of record payload + previous hash for tamper evidence."""
    chain_input = f"{prev_hash or 'GENESIS'}:{payload_json}"
    return hashlib.sha256(chain_input.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Audit record
# ---------------------------------------------------------------------------

class AuditRecord:
    """A single append-only audit record with hash chain."""

    def __init__(
        self,
        id: str,
        upload_hash: str,
        settlement_id: str,
        timestamp: str,
        decision_state: str,
        payload_json: str,
        record_hash: str = "",
        prev_hash: Optional[str] = None,
    ):
        self.id = id
        self.upload_hash = upload_hash
        self.settlement_id = settlement_id
        self.timestamp = timestamp
        self.decision_state = decision_state
        self.payload_json = payload_json
        self.record_hash = record_hash
        self.prev_hash = prev_hash

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "upload_hash": self.upload_hash,
            "settlement_id": self.settlement_id,
            "timestamp": self.timestamp,
            "decision_state": self.decision_state,
            "payload_json": self.payload_json,
            "record_hash": self.record_hash,
            "prev_hash": self.prev_hash or "",
        }

    def payload(self) -> dict[str, Any]:
        """Deserialize the JSON payload."""
        return json.loads(self.payload_json)  # type: ignore[no-any-return]


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
        """Initialize database schema with WAL mode and safe journal."""
        self._conn = sqlite3.connect(self.db_path, timeout=30)
        assert self._conn is not None
        self._conn.execute("PRAGMA journal_mode=WAL")
        assert self._conn is not None
        self._conn.execute(SCHEMA_SQL)
        assert self._conn is not None
        self._conn.execute(INDEX_SQL)
        assert self._conn is not None
        self._conn.execute(INDEX_SETTLEMENT_SQL)
        assert self._conn is not None
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _get_last_hash(self, upload_hash: str) -> Optional[str]:
        """Get the hash of the most recent record for a batch (for chaining)."""
        assert self._conn is not None
        cursor = self._conn.execute(
            "SELECT record_hash FROM audit_log WHERE upload_hash = ? ORDER BY timestamp DESC LIMIT 1",
            (upload_hash,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def log_result(
        self,
        upload_hash: str,
        result: ReconciliationResult,
        extra_payload: Optional[dict[str, Any]] = None,
        validation_result: Any = None,
        evidence_packet: Any = None,
    ) -> AuditRecord:
        """Append a reconciliation result to the audit log with hash chain.

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

        if evidence_packet is not None:
            payload["evidence_packet"] = evidence_packet.model_dump(mode="json")

        if validation_result is not None:
            payload["validation_result"] = {
                "is_valid": validation_result.is_valid,
                "violations": validation_result.violations,
            }

        if extra_payload:
            payload["extra"] = extra_payload

        payload_json = json.dumps(redact_dict(payload), default=str)

        # Hash chain: BEGIN IMMEDIATE ensures exclusive write lock —
        # prevents concurrent reads/inserts from corrupting the chain
        assert self._conn is not None
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            prev_hash = self._get_last_hash(upload_hash)
            record_hash = _compute_record_hash(payload_json, prev_hash)

            record = AuditRecord(
                id=record_id,
                upload_hash=upload_hash,
                settlement_id=result.settlement_id,
                timestamp=timestamp,
                decision_state=result.decision.value,
                payload_json=payload_json,
                record_hash=record_hash,
                prev_hash=prev_hash,
            )

            assert self._conn is not None
            self._conn.execute(
                "INSERT INTO audit_log (id, upload_hash, settlement_id, timestamp, decision_state, payload_json, record_hash, prev_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (record.id, record.upload_hash, record.settlement_id,
                 record.timestamp, record.decision_state, record.payload_json,
                 record.record_hash, record.prev_hash),
            )
            assert self._conn is not None
            self._conn.commit()
        except Exception:
            logger.exception(
                "Failed to write audit record for upload_hash=%s settlement_id=%s; rolling back",
                upload_hash, result.settlement_id,
            )
            if self._conn is not None:
                self._conn.rollback()
            raise

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
        assert self._conn is not None
        cursor = self._conn.execute(
            "SELECT id, upload_hash, settlement_id, timestamp, decision_state, payload_json, record_hash, prev_hash "
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
                record_hash=row[6],
                prev_hash=row[7],
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
        assert self._conn is not None
        cursor = self._conn.execute(
            "SELECT id, upload_hash, settlement_id, timestamp, decision_state, payload_json, record_hash, prev_hash "
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
                record_hash=row[6],
                prev_hash=row[7],
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
        assert self._conn is not None
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
        assert self._conn is not None
        cursor = self._conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE upload_hash = ?",
            (upload_hash,),
        )
        return cursor.fetchone()[0]  # type: ignore[no-any-return]

    def verify_chain(self, upload_hash: str) -> dict[str, Any]:
        """Verify hash chain integrity for a batch.

        Returns:
            Dict with 'valid' bool, 'total_records' count, and 'broken_at' index if invalid.
        """
        records = self.get_batch(upload_hash)
        if not records:
            return {"valid": True, "total_records": 0, "broken_at": None}

        for i, record in enumerate(records):
            expected_hash = _compute_record_hash(record.payload_json, record.prev_hash)
            if record.record_hash != expected_hash:
                return {
                    "valid": False,
                    "total_records": len(records),
                    "broken_at": i,
                    "settlement_id": record.settlement_id,
                    "expected": expected_hash,
                    "actual": record.record_hash,
                }

            # Verify chain continuity
            if i > 0 and record.prev_hash != records[i - 1].record_hash:
                return {
                    "valid": False,
                    "total_records": len(records),
                    "broken_at": i,
                    "settlement_id": record.settlement_id,
                    "error": "Chain break: prev_hash does not match previous record's hash",
                }

        return {"valid": True, "total_records": len(records), "broken_at": None}

    def log_human_review(
        self,
        settlement_id: str,
        review: HumanReviewDecision,
    ) -> AuditRecord:
        """Log a human review decision as an audit record with hash chain.

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

        payload_json = json.dumps(redact_dict(payload), default=str)

        # Hash chain for human_review records with BEGIN IMMEDIATE
        assert self._conn is not None
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            prev_hash = self._get_last_hash("human_review")
            record_hash = _compute_record_hash(payload_json, prev_hash)

            record = AuditRecord(
                id=record_id,
                upload_hash="human_review",
                settlement_id=settlement_id,
                timestamp=timestamp,
                decision_state=payload["decision_state"],
                payload_json=payload_json,
                record_hash=record_hash,
                prev_hash=prev_hash,
            )

            assert self._conn is not None
            self._conn.execute(
                "INSERT INTO audit_log (id, upload_hash, settlement_id, timestamp, decision_state, payload_json, record_hash, prev_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (record.id, record.upload_hash, record.settlement_id,
                 record.timestamp, record.decision_state, record.payload_json,
                 record.record_hash, record.prev_hash),
            )
            assert self._conn is not None
            self._conn.commit()
        except Exception:
            logger.exception(
                "Failed to write human_review audit record for settlement_id=%s; rolling back",
                settlement_id,
            )
            if self._conn is not None:
                self._conn.rollback()
            raise

        return record


# ---------------------------------------------------------------------------
# Convenience: in-memory audit (for testing)
# ---------------------------------------------------------------------------

class InMemoryAuditLogger:
    """In-memory audit logger for testing. No SQLite dependency."""

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def close(self) -> None:
        pass

    def _get_last_hash(self, upload_hash: str) -> Optional[str]:
        """Get the hash of the most recent record for a batch (for chaining)."""
        batch = [r for r in self.records if r.upload_hash == upload_hash]
        return batch[-1].record_hash if batch else None

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

        # Hash chain
        prev_hash = self._get_last_hash(upload_hash)
        record_hash = _compute_record_hash(payload_json, prev_hash)

        record = AuditRecord(
            id=record_id,
            upload_hash=upload_hash,
            settlement_id=result.settlement_id,
            timestamp=timestamp,
            decision_state=result.decision.value,
            payload_json=payload_json,
            record_hash=record_hash,
            prev_hash=prev_hash,
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

    def verify_chain(self, upload_hash: str) -> dict[str, Any]:
        """Verify hash chain integrity (in-memory version)."""
        records = self.get_batch(upload_hash)
        if not records:
            return {"valid": True, "total_records": 0, "broken_at": None}

        for i, record in enumerate(records):
            expected_hash = _compute_record_hash(record.payload_json, record.prev_hash)
            if record.record_hash != expected_hash:
                return {
                    "valid": False,
                    "total_records": len(records),
                    "broken_at": i,
                    "settlement_id": record.settlement_id,
                }
            if i > 0 and record.prev_hash != records[i - 1].record_hash:
                return {
                    "valid": False,
                    "total_records": len(records),
                    "broken_at": i,
                    "settlement_id": record.settlement_id,
                    "error": "Chain break",
                }

        return {"valid": True, "total_records": len(records), "broken_at": None}

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

        # Hash chain
        prev_hash = self._get_last_hash("human_review")
        record_hash = _compute_record_hash(payload_json, prev_hash)

        record = AuditRecord(
            id=record_id,
            upload_hash="human_review",
            settlement_id=settlement_id,
            timestamp=timestamp,
            decision_state=payload["decision_state"],
            payload_json=payload_json,
            record_hash=record_hash,
            prev_hash=prev_hash,
        )

        self.records.append(record)
        return record
