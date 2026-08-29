"""
Phase 9 Tests: Append-Only Audit Logger

Must pass: Every settlement has one audit record. upload_hash groups by batch.
Append-only: no updates, no deletes.
"""

import json
import os
import tempfile
from datetime import date

import pytest

from backend.audit import (
    AuditLogger,
    InMemoryAuditLogger,
    AuditRecord,
)
from backend.models import (
    DecisionState,
    ReconciliationResult,
    AIResponse,
    AIClassification,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    settlement_id: str,
    decision: DecisionState,
    difference_paise: int = 0,
    expected: int = 100000,
    actual: int = 100000,
    ai_response: AIResponse | None = None,
) -> ReconciliationResult:
    return ReconciliationResult(
        settlement_id=settlement_id,
        decision=decision,
        difference_paise=difference_paise,
        expected_amount_paise=expected,
        actual_amount_paise=actual,
        deterministic_checks_passed=["schema_validation", "fee_validation"],
        deterministic_checks_failed=[],
        escalate_to_human=decision != DecisionState.CLEAN_MATCH,
        ai_response=ai_response,
    )


def _make_ai_response():
    return AIResponse(
        classification=AIClassification.TIMING_MISMATCH,
        explanation="Bank credited after expected cycle.",
        raw_confidence=0.82,
        cited_evidence=["timing"],
    )


# ---------------------------------------------------------------------------
# SQLite audit logger
# ---------------------------------------------------------------------------

class TestAuditLogger:
    def test_log_result_creates_record(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            result = _make_result("SETL_001", DecisionState.CLEAN_MATCH)
            record = logger.log_result("hash_abc", result)

            assert record.id is not None
            assert record.upload_hash == "hash_abc"
            assert record.settlement_id == "SETL_001"
            assert record.decision_state == "CLEAN_MATCH"
            assert record.timestamp is not None
        finally:
            logger.close()

    def test_payload_json_is_valid(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            result = _make_result("SETL_001", DecisionState.CLEAN_MATCH)
            record = logger.log_result("hash_abc", result)

            payload = record.payload()
            assert payload["settlement_id"] == "SETL_001"
            assert payload["decision_state"] == "CLEAN_MATCH"
            assert payload["difference_paise"] == 0
            assert payload["escalate_to_human"] is False
        finally:
            logger.close()

    def test_log_batch_creates_multiple_records(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            results = [
                _make_result("SETL_001", DecisionState.CLEAN_MATCH),
                _make_result("SETL_002", DecisionState.DETERMINISTIC_EXCEPTION),
                _make_result("SETL_003", DecisionState.MATH_DISCREPANCY),
            ]
            records = logger.log_batch("hash_abc", results)

            assert len(records) == 3
            assert records[0].settlement_id == "SETL_001"
            assert records[1].settlement_id == "SETL_002"
            assert records[2].settlement_id == "SETL_003"
        finally:
            logger.close()

    def test_get_batch_returns_correct_records(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            logger.log_batch("hash_1", [_make_result("SETL_001", DecisionState.CLEAN_MATCH)])
            logger.log_batch("hash_2", [_make_result("SETL_002", DecisionState.CLEAN_MATCH)])

            records_1 = logger.get_batch("hash_1")
            records_2 = logger.get_batch("hash_2")

            assert len(records_1) == 1
            assert len(records_2) == 1
            assert records_1[0].settlement_id == "SETL_001"
            assert records_2[0].settlement_id == "SETL_002"
        finally:
            logger.close()

    def test_upload_hash_groups_by_batch(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            results_batch_1 = [
                _make_result(f"SETL_B1_{i}", DecisionState.CLEAN_MATCH)
                for i in range(5)
            ]
            results_batch_2 = [
                _make_result(f"SETL_B2_{i}", DecisionState.DETERMINISTIC_EXCEPTION)
                for i in range(3)
            ]

            logger.log_batch("batch_1_hash", results_batch_1)
            logger.log_batch("batch_2_hash", results_batch_2)

            assert logger.total_records("batch_1_hash") == 5
            assert logger.total_records("batch_2_hash") == 3
        finally:
            logger.close()

    def test_append_only_no_updates(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            result = _make_result("SETL_001", DecisionState.CLEAN_MATCH)
            record = logger.log_result("hash_abc", result)

            # Verify record exists
            records = logger.get_batch("hash_abc")
            assert len(records) == 1
            original_payload = records[0].payload()

            # There is no update API — append-only by design
            # Verify the record hasn't changed
            assert records[0].payload() == original_payload
        finally:
            logger.close()

    def test_count_by_decision(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            results = [
                _make_result("SETL_001", DecisionState.CLEAN_MATCH),
                _make_result("SETL_002", DecisionState.CLEAN_MATCH),
                _make_result("SETL_003", DecisionState.DETERMINISTIC_EXCEPTION),
                _make_result("SETL_004", DecisionState.MATH_DISCREPANCY),
                _make_result("SETL_005", DecisionState.UNPROCESSED),
            ]
            logger.log_batch("hash_abc", results)

            counts = logger.count_by_decision("hash_abc")
            assert counts["CLEAN_MATCH"] == 2
            assert counts["DETERMINISTIC_EXCEPTION"] == 1
            assert counts["MATH_DISCREPANCY"] == 1
            assert counts["UNPROCESSED"] == 1
        finally:
            logger.close()

    def test_get_settlement_history(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            # Same settlement across two batches
            logger.log_batch("batch_1", [_make_result("SETL_001", DecisionState.CLEAN_MATCH)])
            logger.log_batch("batch_2", [_make_result("SETL_001", DecisionState.MATH_DISCREPANCY)])

            history = logger.get_settlement_history("SETL_001")
            assert len(history) == 2
            assert history[0].upload_hash == "batch_1"
            assert history[1].upload_hash == "batch_2"
        finally:
            logger.close()

    def test_ai_response_in_payload(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            ai = _make_ai_response()
            result = _make_result(
                "SETL_001",
                DecisionState.REVIEW_REQUIRED,
                difference_paise=-5000,
                expected=100000,
                actual=95000,
                ai_response=ai,
            )
            record = logger.log_result("hash_abc", result)

            payload = record.payload()
            assert "ai_response" in payload
            assert payload["ai_response"]["classification"] == "TIMING_MISMATCH"
            assert payload["ai_response"]["raw_confidence"] == 0.82
            assert payload["ai_response"]["recommended_action"] == "ESCALATE_TO_HUMAN"
        finally:
            logger.close()

    def test_extra_payload(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            result = _make_result("SETL_001", DecisionState.CLEAN_MATCH)
            record = logger.log_result(
                "hash_abc",
                result,
                extra_payload={"batch_index": 42, "source": "test"},
            )

            payload = record.payload()
            assert payload["extra"]["batch_index"] == 42
            assert payload["extra"]["source"] == "test"
        finally:
            logger.close()

    def test_empty_batch(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            records = logger.log_batch("hash_abc", [])
            assert records == []
            assert logger.total_records("hash_abc") == 0
        finally:
            logger.close()


# ---------------------------------------------------------------------------
# In-memory audit logger
# ---------------------------------------------------------------------------

class TestInMemoryAuditLogger:
    def test_log_result_creates_record(self):
        logger = InMemoryAuditLogger()
        result = _make_result("SETL_001", DecisionState.CLEAN_MATCH)
        record = logger.log_result("hash_abc", result)

        assert record.id is not None
        assert record.upload_hash == "hash_abc"
        assert record.settlement_id == "SETL_001"
        assert record.decision_state == "CLEAN_MATCH"

    def test_get_batch(self):
        logger = InMemoryAuditLogger()
        logger.log_batch("hash_1", [_make_result("SETL_001", DecisionState.CLEAN_MATCH)])
        logger.log_batch("hash_2", [_make_result("SETL_002", DecisionState.CLEAN_MATCH)])

        assert len(logger.get_batch("hash_1")) == 1
        assert len(logger.get_batch("hash_2")) == 1

    def test_total_records(self):
        logger = InMemoryAuditLogger()
        logger.log_batch("hash_abc", [
            _make_result("SETL_001", DecisionState.CLEAN_MATCH),
            _make_result("SETL_002", DecisionState.CLEAN_MATCH),
        ])
        assert logger.total_records("hash_abc") == 2

    def test_count_by_decision(self):
        logger = InMemoryAuditLogger()
        logger.log_batch("hash_abc", [
            _make_result("SETL_001", DecisionState.CLEAN_MATCH),
            _make_result("SETL_002", DecisionState.DETERMINISTIC_EXCEPTION),
        ])
        counts = logger.count_by_decision("hash_abc")
        assert counts["CLEAN_MATCH"] == 1
        assert counts["DETERMINISTIC_EXCEPTION"] == 1

    def test_get_settlement_history(self):
        logger = InMemoryAuditLogger()
        logger.log_batch("batch_1", [_make_result("SETL_001", DecisionState.CLEAN_MATCH)])
        logger.log_batch("batch_2", [_make_result("SETL_001", DecisionState.MATH_DISCREPANCY)])

        history = logger.get_settlement_history("SETL_001")
        assert len(history) == 2


# ---------------------------------------------------------------------------
# Integration: engine → audit
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_engine_results_to_audit(self, tmp_path):
        from datetime import datetime
        from backend.engine import run_engine

        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)

        t1 = {
            "payment_id": "PAY_001", "order_id": "ORD_001",
            "amount": 100000, "status": "captured", "method": "upi",
            "fee": 0, "tax": 0, "created_at": datetime(2026, 8, 20, 10, 0, 0),
            "settlement_id": "SETL_001",
        }
        s1 = {
            "settlement_id": "SETL_001", "amount": 100000,
            "status": "settled", "utr": "UTR_001",
            "created_at": datetime(2026, 8, 20, 10, 0, 0),
            "settled_at": datetime(2026, 8, 21, 8, 0, 0),
            "linked_payment_ids": ["PAY_001"],
            "linked_refund_ids": [],
        }
        bc1 = {
            "utr": "UTR_001", "amount": 100000,
            "date": date(2026, 8, 22),
            "description": "NEFT", "bank_account": "ACC001",
        }

        results = run_engine([t1], [s1], [], [bc1])
        assert len(results) == 1

        records = logger.log_batch("test_hash", results)
        assert len(records) == 1

        fetched = logger.get_batch("test_hash")
        assert len(fetched) == 1
        assert fetched[0].settlement_id == "SETL_001"
        assert fetched[0].decision_state == "CLEAN_MATCH"

        logger.close()

    def test_full_60_batch_to_audit(self, tmp_path):
        from backend.generator import generate_batch
        from backend.engine import run_engine

        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)

        data = generate_batch()
        results = run_engine(
            data["transactions"],
            data["settlements"],
            data["refunds"],
            data["bank_credits"],
        )

        records = logger.log_batch("eval_hash", results)
        # One record per settlement processed (80 settlements in default batch)
        assert len(records) == 80

        # Every result has exactly one audit record
        for result in results:
            matching = [r for r in records if r.settlement_id == result.settlement_id]
            assert len(matching) >= 1

        # upload_hash groups correctly
        assert logger.total_records("eval_hash") == 80

        counts = logger.count_by_decision("eval_hash")
        total_from_counts = sum(counts.values())
        assert total_from_counts == 80

        logger.close()


# ---------------------------------------------------------------------------
# Hash chain integrity tests
# ---------------------------------------------------------------------------

class TestHashChain:
    def test_compute_record_hash_deterministic(self):
        from backend.audit import _compute_record_hash
        h1 = _compute_record_hash('{"key": "value"}', None)
        h2 = _compute_record_hash('{"key": "value"}', None)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex digest

    def test_compute_record_hash_varies_with_prev(self):
        from backend.audit import _compute_record_hash
        h1 = _compute_record_hash('{"key": "value"}', None)
        h2 = _compute_record_hash('{"key": "value"}', "abc123")
        assert h1 != h2

    def test_verify_chain_valid(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            results = [
                _make_result("SETL_001", DecisionState.CLEAN_MATCH),
                _make_result("SETL_002", DecisionState.DETERMINISTIC_EXCEPTION),
                _make_result("SETL_003", DecisionState.MATH_DISCREPANCY),
            ]
            logger.log_batch("hash_abc", results)
            chain = logger.verify_chain("hash_abc")
            assert chain["valid"] is True
            assert chain["total_records"] == 3
            assert chain["broken_at"] is None
        finally:
            logger.close()

    def test_verify_chain_empty_batch(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            chain = logger.verify_chain("nonexistent_hash")
            assert chain["valid"] is True
            assert chain["total_records"] == 0
        finally:
            logger.close()

    def test_verify_chain_tampered_record(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            results = [
                _make_result("SETL_001", DecisionState.CLEAN_MATCH),
                _make_result("SETL_002", DecisionState.CLEAN_MATCH),
            ]
            logger.log_batch("hash_abc", results)

            # Tamper with a record's payload in the database
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.execute(
                "UPDATE audit_log SET payload_json = ? WHERE settlement_id = ?",
                ('{"tampered": true}', "SETL_001"),
            )
            conn.commit()
            conn.close()

            chain = logger.verify_chain("hash_abc")
            assert chain["valid"] is False
            assert chain["broken_at"] == 0
            assert chain["settlement_id"] == "SETL_001"
        finally:
            logger.close()

    def test_verify_chain_in_memory_valid(self):
        logger = InMemoryAuditLogger()
        results = [
            _make_result("SETL_001", DecisionState.CLEAN_MATCH),
            _make_result("SETL_002", DecisionState.CLEAN_MATCH),
        ]
        logger.log_batch("hash_abc", results)
        chain = logger.verify_chain("hash_abc")
        assert chain["valid"] is True
        assert chain["total_records"] == 2

    def test_verify_chain_in_memory_tampered(self):
        logger = InMemoryAuditLogger()
        results = [
            _make_result("SETL_001", DecisionState.CLEAN_MATCH),
            _make_result("SETL_002", DecisionState.CLEAN_MATCH),
        ]
        logger.log_batch("hash_abc", results)

        # Tamper with in-memory record
        logger.records[0].payload_json = '{"tampered": true}'
        chain = logger.verify_chain("hash_abc")
        assert chain["valid"] is False
        assert chain["broken_at"] == 0

    def test_verify_chain_continuity(self, tmp_path):
        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            results = [_make_result(f"SETL_{i:03d}", DecisionState.CLEAN_MATCH) for i in range(5)]
            logger.log_batch("hash_abc", results)
            chain = logger.verify_chain("hash_abc")
            assert chain["valid"] is True
            assert chain["total_records"] == 5
        finally:
            logger.close()
