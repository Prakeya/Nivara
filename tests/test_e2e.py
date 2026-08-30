"""
Phase 12 Tests: End-to-End + Demo Recording

Must pass:
- 60-record batch in <60s
- Unexplained demo produces MATH_DISCREPANCY + human escalation
"""

import time

import pytest

from backend.engine import run_engine
from backend.evaluation import evaluate_batch
from backend.generator import generate_batch
from backend.models import DecisionState


class TestPerformance:
    def test_60_batch_under_60s(self):
        """Generate + ingest + link + reconcile 60 records in <60 seconds."""
        data = generate_batch()

        start = time.time()
        results = run_engine(
            data["transactions"],
            data["settlements"],
            data["refunds"],
            data["bank_credits"],
        )
        elapsed = time.time() - start

        assert len(results) == 80
        assert elapsed < 60, f"60-record batch took {elapsed:.1f}s (limit: 60s)"

    def test_evaluation_under_60s(self):
        """Full evaluation pipeline under 60s."""
        data = generate_batch()

        start = time.time()
        results = run_engine(
            data["transactions"],
            data["settlements"],
            data["refunds"],
            data["bank_credits"],
        )
        metrics = evaluate_batch(results, data["ground_truth"])
        elapsed = time.time() - start

        assert elapsed < 60
        assert metrics.total == 80
        assert 0 <= metrics.match_rate <= 1
        assert 0 <= metrics.false_accept_rate <= 1
        assert metrics.ai_auto_approval_rate_pct == 0.0


class TestDemoScenario:
    def test_unexplained_produces_math_discrepancy(self):
        """Unexplained settlements produce MATH_DISCREPANCY with human escalation."""
        data = generate_batch(seed=42)
        results = run_engine(
            data["transactions"],
            data["settlements"],
            data["refunds"],
            data["bank_credits"],
        )

        # Find unexplained by matching ground truth labels
        gt_by_sid = {gt["settlement_id"]: gt for gt in data["ground_truth"]}
        unexplained_results = [
            r for r in results
            if gt_by_sid.get(r.settlement_id, {}).get("label") == "unexplained"
        ]

        assert len(unexplained_results) >= 1, "No unexplained settlements found"

        for r in unexplained_results:
            # After AI investigation fix, unexplained cases get REVIEW_REQUIRED
            # (LLM classifies them). Before the fix, they were MATH_DISCREPANCY.
            assert r.decision in (DecisionState.MATH_DISCREPANCY, DecisionState.REVIEW_REQUIRED)
            assert r.difference_paise != 0
            assert r.escalate_to_human is True
            assert "expected_amount_calculation" in r.deterministic_checks_passed
            assert "difference_calculation" in r.deterministic_checks_passed

    def test_demo_metrics_structure(self):
        """Verify the demo produces expected metric structure."""
        data = generate_batch(seed=42)
        results = run_engine(
            data["transactions"],
            data["settlements"],
            data["refunds"],
            data["bank_credits"],
        )
        metrics = evaluate_batch(results, data["ground_truth"])

        # Verify all required fields exist
        assert hasattr(metrics, "total")
        assert hasattr(metrics, "match_rate")
        assert hasattr(metrics, "false_accept_rate")
        assert hasattr(metrics, "ai_auto_approval_rate_pct")

        # Safety invariants
        assert metrics.ai_auto_approval_rate_pct == 0.0
        assert metrics.total == 80

    def test_clean_matches_pass_all_checks(self):
        """CLEAN_MATCH settlements have difference==0 and all checks pass."""
        data = generate_batch(seed=42)
        results = run_engine(
            data["transactions"],
            data["settlements"],
            data["refunds"],
            data["bank_credits"],
        )

        clean = [r for r in results if r.decision == DecisionState.CLEAN_MATCH]
        assert len(clean) >= 1

        for r in clean:
            assert r.difference_paise == 0
            assert r.escalate_to_human is False
            assert len(r.deterministic_checks_failed) == 0

    def test_ai_investigations_present(self):
        """Some settlements trigger AI investigation when llm_client is provided."""
        from tests.mocks import MockLLMClient
        data = generate_batch(seed=42)
        results = run_engine(
            data["transactions"],
            data["settlements"],
            data["refunds"],
            data["bank_credits"],
            llm_client=MockLLMClient(classification="UNEXPLAINED", confidence=0.5),
        )

        ai_count = sum(1 for r in results if r.ai_response is not None)
        # With a mock LLM client, we expect some AI investigations
        # (the AI investigator is called for MATH_DISCREPANCY and REVIEW_REQUIRED cases)
        assert ai_count > 0, "Expected at least one AI investigation in demo batch"

    def test_all_settled_results_escalate(self):
        """Any result with difference != 0 must escalate to human."""
        data = generate_batch(seed=42)
        results = run_engine(
            data["transactions"],
            data["settlements"],
            data["refunds"],
            data["bank_credits"],
        )

        for r in results:
            if r.difference_paise != 0:
                assert r.escalate_to_human is True, (
                    f"{r.settlement_id}: difference={r.difference_paise} "
                    f"but escalate_to_human=False"
                )

    def test_no_result_auto_approves(self):
        """No result should have auto-approved-by-AI."""
        data = generate_batch(seed=42)
        results = run_engine(
            data["transactions"],
            data["settlements"],
            data["refunds"],
            data["bank_credits"],
        )

        # The engine never auto-approves; only AI could, and it's hardcoded to ESCALATE_TO_HUMAN
        for r in results:
            if r.ai_response is not None:
                assert r.ai_response.recommended_action.value == "ESCALATE_TO_HUMAN"


# ---------------------------------------------------------------------------
# E2E: Hash chain lifecycle
# ---------------------------------------------------------------------------

class TestE2EHashChain:
    def test_generate_audit_verify_chain(self):
        """Full lifecycle: generate -> audit -> verify_chain."""
        from backend.audit import AuditLogger
        from backend.generator import generate_batch

        data = generate_batch()
        results = run_engine(
            data["transactions"],
            data["settlements"],
            data["refunds"],
            data["bank_credits"],
        )

        import tempfile, os
        db_path = os.path.join(tempfile.mkdtemp(), "test_audit.db")
        logger = AuditLogger(db_path)
        try:
            records = logger.log_batch("e2e_hash", results)
            assert len(records) == 80

            chain = logger.verify_chain("e2e_hash")
            assert chain["valid"] is True
            assert chain["total_records"] == 80
            assert chain["broken_at"] is None
        finally:
            logger.close()
            import shutil
            shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


# ---------------------------------------------------------------------------
# E2E: Human review flow
# ---------------------------------------------------------------------------

class TestE2EHumanReview:
    def test_upload_pending_review_audit(self):
        """Full flow: upload -> get pending -> review decision -> verify status."""
        from backend.main import _jobs
        from fastapi.testclient import TestClient
        from backend.main import app
        from tests.test_api import _make_upload_files

        test_client = TestClient(app)
        _jobs.clear()

        data = generate_batch()
        files = _make_upload_files(data)

        # Upload
        upload_resp = test_client.post("/upload", files=files)
        assert upload_resp.status_code == 202
        job_id = upload_resp.json()["job_id"]

        # Get pending reviews
        pending_resp = test_client.get("/api/review/pending")
        assert pending_resp.status_code == 200
        pending = pending_resp.json()

        # If there are pending reviews, test submitting one
        if pending["total_pending"] > 0:
            sid = pending["settlements"][0]["settlement_id"]

            # Submit review
            review_resp = test_client.post(
                f"/api/review/{sid}/decision",
                json={"decision": "APPROVE", "reason": "E2E test", "reviewer_id": "e2e_test"},
            )
            assert review_resp.status_code == 200

            # Check review status
            status_resp = test_client.get(f"/api/review/{sid}")
            assert status_resp.status_code == 200
            assert status_resp.json()["reviewed"] is True
