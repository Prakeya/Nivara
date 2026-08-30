"""
End-to-end integration tests (Phase 2 STEP 2.1).

Each test drives the full pipeline: generated CSVs -> ingestion ->
deterministic engine -> (AI investigation) -> audit log.

The AI layer is always mocked so tests run offline and are deterministic.
"""

import csv
import json

import pytest
from unittest.mock import patch

from backend.engine import run_engine
from backend.ingestion import ingest_csvs
from backend.audit import AuditLogger
from backend.generator import generate_batch
from backend.csv_schema import CSV_SCHEMAS
from backend.models import (
    AIResponse,
    AIClassification,
    DecisionState,
    ReconciliationResult,
)


def _schema_columns(csv_name: str) -> list[str]:
    return [c["name"] for c in CSV_SCHEMAS[csv_name]["required_columns"]]


def _write_csvs(tmp_path, data: dict) -> str:
    """Write the four generated CSV datasets into tmp_path and return the path."""
    for name in ["transactions", "settlements", "refunds", "bank_credits"]:
        rows = data[name]
        path = tmp_path / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            columns = list(rows[0].keys()) if rows else _schema_columns(name)
            writer = csv.DictWriter(fh, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                serialized = {
                    k: json.dumps(v) if isinstance(v, list) else v
                    for k, v in row.items()
                }
                writer.writerow(serialized)
    return str(tmp_path)


_DUMMY_LLM = object()  # truthy -> engine enables the AI investigation path


def _fake_ai_response(evidence_packet_v2, **kwargs) -> AIResponse:
    """Build an AIResponse whose citations are all valid packet evidence IDs."""
    valid = evidence_packet_v2.get_valid_citation_ids()
    assert valid, "expected at least one valid citation id in evidence packet"
    return AIResponse(
        classification=AIClassification.UNEXPLAINED,
        explanation="Simulated AI investigation result.",
        raw_confidence=0.85,
        cited_evidence=[sorted(valid)[0]],
    )


class TestPipelineIntegration:
    def test_clean_match_deterministic(self, tmp_path):
        """Clean matches resolve deterministically; AI is never invoked."""
        data = generate_batch(n_settlements=8, edge_cases={"clean_match": 8}, seed=1)
        result = ingest_csvs(
            *(f"{_write_csvs(tmp_path, data)}/{n}.csv" for n in
              ["transactions", "settlements", "refunds", "bank_credits"])
        )
        assert result.is_valid

        with patch("backend.ai_investigator.investigate_v2") as mock_inv:
            results = run_engine(
                result.transactions,
                result.settlements,
                result.refunds,
                result.bank_credits,
                llm_client=_DUMMY_LLM,
            )

        assert len(results) == 8
        assert all(r.decision == DecisionState.CLEAN_MATCH for r in results)
        assert all((r.deterministic_checks_passed and not r.deterministic_checks_failed)
                   for r in results)
        assert all(r.ai_response is None for r in results)
        assert all(r.escalate_to_human is False for r in results)
        mock_inv.assert_not_called()

    def test_deterministic_exception_resolution(self, tmp_path):
        """Fee evidence produces a deterministic exception; AI is never invoked."""
        data = generate_batch(n_settlements=8, edge_cases={"fee_mismatch": 8}, seed=1)
        result = ingest_csvs(
            *(f"{_write_csvs(tmp_path, data)}/{n}.csv" for n in
              ["transactions", "settlements", "refunds", "bank_credits"])
        )
        assert result.is_valid

        with patch("backend.ai_investigator.investigate_v2") as mock_inv:
            results = run_engine(
                result.transactions,
                result.settlements,
                result.refunds,
                result.bank_credits,
                llm_client=_DUMMY_LLM,
            )

        assert len(results) == 8
        assert all(r.decision == DecisionState.DETERMINISTIC_EXCEPTION for r in results)
        assert all(r.ai_response is None for r in results)
        assert all(r.escalate_to_human is True for r in results)
        assert all(r.evidence_packet is not None and r.evidence_packet.fee_evidence is not None
                   for r in results)
        mock_inv.assert_not_called()

    def test_math_discrepancy_triggers_ai(self, tmp_path):
        """Unexplained discrepancies invoke AI and attach a validated response."""
        data = generate_batch(n_settlements=8, edge_cases={"unexplained": 8}, seed=1)
        result = ingest_csvs(
            *(f"{_write_csvs(tmp_path, data)}/{n}.csv" for n in
              ["transactions", "settlements", "refunds", "bank_credits"])
        )
        assert result.is_valid

        with patch("backend.ai_investigator.investigate_v2",
                   side_effect=_fake_ai_response) as mock_inv:
            results = run_engine(
                result.transactions,
                result.settlements,
                result.refunds,
                result.bank_credits,
                llm_client=_DUMMY_LLM,
            )

        assert mock_inv.call_count == 8
        assert len(results) == 8
        for r in results:
            assert r.decision == DecisionState.MATH_DISCREPANCY
            assert r.ai_response is not None
            assert r.ai_mode == "live"
            assert r.resolution_source == "ai"
            assert r.escalate_to_human is True
            # Citations must be verifiable against the evidence packet
            valid = r.evidence_packet.get_valid_citation_ids()
            assert set(r.ai_response.cited_evidence).issubset(valid)

    def test_ai_validation_failure_unresolved(self, tmp_path):
        """When AI validation fails, the settlement becomes UNRESOLVED."""
        from backend.ai_investigator import LLMMalformedResponseError

        data = generate_batch(n_settlements=8, edge_cases={"unexplained": 8}, seed=1)
        result = ingest_csvs(
            *(f"{_write_csvs(tmp_path, data)}/{n}.csv" for n in
              ["transactions", "settlements", "refunds", "bank_credits"])
        )
        assert result.is_valid

        def _boom(*args, **kwargs):
            raise LLMMalformedResponseError("malformed JSON from LLM")

        with patch("backend.ai_investigator.investigate_v2", side_effect=_boom) as mock_inv:
            results = run_engine(
                result.transactions,
                result.settlements,
                result.refunds,
                result.bank_credits,
                llm_client=_DUMMY_LLM,
            )

        assert mock_inv.call_count == 8
        assert len(results) == 8
        assert all(r.decision == DecisionState.UNRESOLVED for r in results)
        assert all(r.ai_response is None for r in results)
        assert all(r.escalate_to_human is True for r in results)
        assert all(r.resolution_source is None for r in results)

    def test_audit_chain_integrity(self, tmp_path):
        """Every result is appended to an append-only audit chain with links intact."""
        data = generate_batch(n_settlements=8, edge_cases={"clean_match": 3, "unexplained": 5}, seed=1)
        result = ingest_csvs(
            *(f"{_write_csvs(tmp_path, data)}/{n}.csv" for n in
              ["transactions", "settlements", "refunds", "bank_credits"])
        )
        assert result.is_valid

        results = run_engine(
            result.transactions,
            result.settlements,
            result.refunds,
            result.bank_credits,
            llm_client=None,  # AI off: non-clean settle to human review
        )

        db_path = str(tmp_path / "audit.db")
        logger = AuditLogger(db_path)
        try:
            records = logger.log_batch(result.upload_hash, results)
            stored = logger.get_batch(result.upload_hash)
            verification_clean = logger.verify_chain(result.upload_hash)

            # Tamper with the earliest record's payload
            import sqlite3 as _sqlite3

            conn = _sqlite3.connect(db_path)
            target_id = stored[0].id
            conn.execute(
                "UPDATE audit_log SET payload_json = '{\"tampered\": true}' WHERE id = ?",
                (target_id,),
            )
            conn.commit()
            conn.close()

            verification_tampered = logger.verify_chain(result.upload_hash)
        finally:
            logger.close()

        assert len(records) == 8
        assert len(stored) == 8
        # First record seeds the chain; every subsequent link is verified
        assert stored[0].prev_hash is None
        for record in stored[1:]:
            assert record.prev_hash == stored[stored.index(record) - 1].record_hash
        for record in stored:
            assert record.upload_hash == result.upload_hash
            assert record.decision_state in (d.value for d in DecisionState)
            assert record.record_hash
            assert record.payload_json

        assert verification_clean["valid"] is True
        assert verification_clean["total_records"] == 8
        assert verification_tampered["valid"] is False
        assert verification_tampered["broken_at"] == 0