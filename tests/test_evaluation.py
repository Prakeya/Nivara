"""
Phase 6 Tests: Evaluation Harness

Must pass: Evaluation harness scores batch correctly. False accept rate calculable.
"""

from backend.evaluation import (
    evaluate_batch,
    format_report,
    format_label_breakdown,
    EvaluationMetrics,
    CLEAN_LABELS,
    EXCEPTION_LABELS,
)
from backend.models import DecisionState, ReconciliationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    settlement_id: str,
    decision: DecisionState,
    difference_paise: int = 0,
    expected_amount_paise: int = 100000,
    actual_amount_paise: int = 100000,
) -> ReconciliationResult:
    return ReconciliationResult(
        settlement_id=settlement_id,
        decision=decision,
        difference_paise=difference_paise,
        expected_amount_paise=expected_amount_paise,
        actual_amount_paise=actual_amount_paise,
        deterministic_checks_passed=["schema_validation"],
        deterministic_checks_failed=[],
        escalate_to_human=decision != DecisionState.CLEAN_MATCH,
    )


def _make_gt(settlement_id: str, label: str) -> dict:
    return {"settlement_id": settlement_id, "label": label}


# ---------------------------------------------------------------------------
# Label taxonomy
# ---------------------------------------------------------------------------

class TestLabelTaxonomy:
    def test_clean_labels(self):
        assert CLEAN_LABELS == {"clean_match"}

    def test_exception_labels(self):
        expected = {
            "missing_reference", "bank_mismatch",
            "fee_mismatch", "tax_inconsistency", "refund_timing", "unexplained",
            "adjustment_entry", "refund_after_settlement", "timing_race", "partial_settlement",
            "duplicate_detection",
        }
        assert EXCEPTION_LABELS == expected

    def test_no_overlap(self):
        assert CLEAN_LABELS.isdisjoint(EXCEPTION_LABELS)


# ---------------------------------------------------------------------------
# Basic evaluation
# ---------------------------------------------------------------------------

class TestEvaluateBatch:
    def test_all_clean_match(self):
        results = [_make_result(f"S{i}", DecisionState.CLEAN_MATCH) for i in range(5)]
        gt = [_make_gt(f"S{i}", "clean_match") for i in range(5)]
        m = evaluate_batch(results, gt)
        assert m.total == 5
        assert m.true_positives == 5
        assert m.false_positives == 0
        assert m.true_negatives == 0
        assert m.false_negatives == 0
        assert m.match_rate == 1.0
        assert m.false_accept_rate == 0.0

    def test_all_exceptions_caught(self):
        results = [_make_result(f"S{i}", DecisionState.DETERMINISTIC_EXCEPTION) for i in range(5)]
        gt = [_make_gt(f"S{i}", "fee_mismatch") for i in range(5)]
        m = evaluate_batch(results, gt)
        assert m.total == 5
        assert m.true_positives == 0
        assert m.false_positives == 0
        assert m.true_negatives == 5
        assert m.false_negatives == 0
        assert m.match_rate == 1.0

    def test_false_positive(self):
        """clean_match ground truth but engine says exception (over-escalated)."""
        results = [_make_result("S1", DecisionState.DETERMINISTIC_EXCEPTION)]
        gt = [_make_gt("S1", "clean_match")]
        m = evaluate_batch(results, gt)
        assert m.false_positives == 1
        assert m.true_positives == 0
        # False accept rate is 0 — this is over-escalation, not a missed exception
        assert m.false_accept_rate == 0.0
        # Safe escalation rate includes over-escalations
        assert m.safe_escalation_rate == 1.0

    def test_false_negative(self):
        """exception ground truth but engine says CLEAN_MATCH (missed!)."""
        results = [_make_result("S1", DecisionState.CLEAN_MATCH)]
        gt = [_make_gt("S1", "fee_mismatch")]
        m = evaluate_batch(results, gt)
        assert m.false_negatives == 1
        assert m.true_negatives == 0

    def test_mixed_batch(self):
        results = [
            _make_result("S1", DecisionState.CLEAN_MATCH),
            _make_result("S2", DecisionState.CLEAN_MATCH),
            _make_result("S3", DecisionState.DETERMINISTIC_EXCEPTION),
            _make_result("S4", DecisionState.DETERMINISTIC_EXCEPTION),
            _make_result("S5", DecisionState.DETERMINISTIC_EXCEPTION),
        ]
        gt = [
            _make_gt("S1", "clean_match"),
            _make_gt("S2", "clean_match"),
            _make_gt("S3", "fee_mismatch"),
            _make_gt("S4", "tax_inconsistency"),
            _make_gt("S5", "unexplained"),
        ]
        m = evaluate_batch(results, gt)
        assert m.total == 5
        assert m.true_positives == 2
        assert m.true_negatives == 3
        assert m.false_positives == 0
        assert m.false_negatives == 0
        assert m.match_rate == 1.0


# ---------------------------------------------------------------------------
# Match rate / false accept rate
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_match_rate_formula(self):
        results = [
            _make_result("S1", DecisionState.CLEAN_MATCH),
            _make_result("S2", DecisionState.CLEAN_MATCH),
            _make_result("S3", DecisionState.DETERMINISTIC_EXCEPTION),
            _make_result("S4", DecisionState.DETERMINISTIC_EXCEPTION),
        ]
        gt = [
            _make_gt("S1", "clean_match"),
            _make_gt("S2", "clean_match"),
            _make_gt("S3", "fee_mismatch"),
            _make_gt("S4", "unexplained"),
        ]
        m = evaluate_batch(results, gt)
        # (TP + TN) / total = (2 + 2) / 4 = 1.0
        assert m.match_rate == 1.0

    def test_false_accept_rate_formula(self):
        results = [
            _make_result("S1", DecisionState.CLEAN_MATCH),
            _make_result("S2", DecisionState.CLEAN_MATCH),
        ]
        gt = [
            _make_gt("S1", "clean_match"),
            _make_gt("S2", "fee_mismatch"),  # should be exception!
        ]
        m = evaluate_batch(results, gt)
        # FP = 1 (S2 is exception but classified as clean)
        # false_accept_rate = FP / total = 1 / 2 = 0.5
        assert m.false_accept_rate == 0.5

    def test_safe_escalation_rate(self):
        results = [
            _make_result("S1", DecisionState.CLEAN_MATCH),
            _make_result("S2", DecisionState.DETERMINISTIC_EXCEPTION),
        ]
        gt = [
            _make_gt("S1", "clean_match"),
            _make_gt("S2", "fee_mismatch"),
        ]
        m = evaluate_batch(results, gt)
        # safe_escalation = (FP + TN + FN) / total = (0 + 1 + 0) / 2 = 0.5
        assert m.safe_escalation_rate == 0.5


# ---------------------------------------------------------------------------
# Processing time
# ---------------------------------------------------------------------------

class TestProcessingTime:
    def test_batch_time(self):
        results = [_make_result(f"S{i}", DecisionState.CLEAN_MATCH) for i in range(10)]
        gt = [_make_gt(f"S{i}", "clean_match") for i in range(10)]
        m = evaluate_batch(results, gt, batch_time_seconds=5.0)
        assert m.batch_time_seconds == 5.0
        assert m.processing_time_per_settlement == 0.5

    def test_zero_time(self):
        results = [_make_result("S1", DecisionState.CLEAN_MATCH)]
        gt = [_make_gt("S1", "clean_match")]
        m = evaluate_batch(results, gt)
        assert m.batch_time_seconds == 0.0
        assert m.processing_time_per_settlement == 0.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_batch(self):
        m = evaluate_batch([], [])
        assert m.total == 0
        assert m.match_rate == 0.0
        assert m.false_accept_rate == 0.0

    def test_mismatched_lengths_raises(self):
        results = [_make_result("S1", DecisionState.CLEAN_MATCH)]
        gt = [_make_gt("S1", "clean_match"), _make_gt("S2", "fee_mismatch")]
        try:
            evaluate_batch(results, gt)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

    def test_missing_ground_truth_raises(self):
        results = [_make_result("S1", DecisionState.CLEAN_MATCH)]
        gt = [_make_gt("S999", "clean_match")]  # wrong ID
        try:
            evaluate_batch(results, gt)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Label breakdown
# ---------------------------------------------------------------------------

class TestLabelBreakdown:
    def test_breakdown_counts(self):
        results = [
            _make_result("S1", DecisionState.CLEAN_MATCH),
            _make_result("S2", DecisionState.CLEAN_MATCH),
            _make_result("S3", DecisionState.DETERMINISTIC_EXCEPTION),
            _make_result("S4", DecisionState.DETERMINISTIC_EXCEPTION),
            _make_result("S5", DecisionState.DETERMINISTIC_EXCEPTION),
        ]
        gt = [
            _make_gt("S1", "clean_match"),
            _make_gt("S2", "clean_match"),
            _make_gt("S3", "fee_mismatch"),
            _make_gt("S4", "fee_mismatch"),
            _make_gt("S5", "tax_inconsistency"),
        ]
        m = evaluate_batch(results, gt)
        assert m.label_counts["clean_match"] == 2
        assert m.label_counts["fee_mismatch"] == 2
        assert m.label_counts["tax_inconsistency"] == 1
        assert m.label_correct["clean_match"] == 2
        assert m.label_correct["fee_mismatch"] == 2
        assert m.label_correct["tax_inconsistency"] == 1


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

class TestFormatReport:
    def test_report_contains_key_info(self):
        results = [
            _make_result("S1", DecisionState.CLEAN_MATCH),
            _make_result("S2", DecisionState.DETERMINISTIC_EXCEPTION),
        ]
        gt = [
            _make_gt("S1", "clean_match"),
            _make_gt("S2", "fee_mismatch"),
        ]
        m = evaluate_batch(results, gt, batch_time_seconds=1.0)
        report = format_report(m)
        assert "60" not in report  # not hardcoded
        assert "2 settlements" in report
        assert "match rate" in report
        assert "100.0%" in report

    def test_report_with_false_positives(self):
        results = [
            _make_result("S1", DecisionState.CLEAN_MATCH),
            _make_result("S2", DecisionState.CLEAN_MATCH),
        ]
        gt = [
            _make_gt("S1", "clean_match"),
            _make_gt("S2", "fee_mismatch"),
        ]
        m = evaluate_batch(results, gt)
        report = format_report(m)
        assert "false accept rate" in report

    def test_report_with_timing(self):
        results = [_make_result("S1", DecisionState.CLEAN_MATCH)]
        gt = [_make_gt("S1", "clean_match")]
        m = evaluate_batch(results, gt, batch_time_seconds=2.5)
        report = format_report(m)
        assert "2.50 seconds" in report

    def test_label_breakdown_format(self):
        results = [
            _make_result("S1", DecisionState.CLEAN_MATCH),
            _make_result("S2", DecisionState.DETERMINISTIC_EXCEPTION),
        ]
        gt = [
            _make_gt("S1", "clean_match"),
            _make_gt("S2", "fee_mismatch"),
        ]
        m = evaluate_batch(results, gt)
        breakdown = format_label_breakdown(m)
        assert "clean_match" in breakdown
        assert "fee_mismatch" in breakdown


# ---------------------------------------------------------------------------
# Integration with generator + engine
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_generator_to_evaluation(self):
        from backend.generator import generate_batch
        from backend.engine import run_engine

        data = generate_batch(n_settlements=10, edge_cases={
            "clean_match": 5,
            "missing_reference": 1,
            "duplicate_settlement": 0,
            "bank_mismatch": 1,
            "fee_mismatch": 1,
            "tax_inconsistency": 1,
            "refund_timing": 1,
            "unexplained": 0,
        })

        results = run_engine(
            data["transactions"],
            data["settlements"],
            data["refunds"],
            data["bank_credits"],
        )

        m = evaluate_batch(results, data["ground_truth"], batch_time_seconds=1.0)
        assert m.total == 10
        assert 0.0 <= m.match_rate <= 1.0
        assert 0.0 <= m.false_accept_rate <= 1.0
        # False accept rate is calculable
        assert isinstance(m.false_accept_rate, float)

    def test_full_60_batch(self):
        from backend.generator import generate_batch
        from backend.engine import run_engine

        data = generate_batch()
        results = run_engine(
            data["transactions"],
            data["settlements"],
            data["refunds"],
            data["bank_credits"],
        )

        m = evaluate_batch(results, data["ground_truth"], batch_time_seconds=48.0)
        assert m.total == 80
        assert 0.0 <= m.match_rate <= 1.0
        assert 0.0 <= m.false_accept_rate <= 1.0
        report = format_report(m)
        assert "80 settlements" in report
