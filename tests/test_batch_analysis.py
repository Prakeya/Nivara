"""
Phase 8 Tests: Batch-Level AI Pattern Analysis

Must pass: Batch analyzer detects at least one pattern on synthetic data.
"""

from backend.batch_analyzer import (
    analyze_batch,
    format_patterns,
    PatternType,
    DetectedPattern,
)
from backend.models import DecisionState, ReconciliationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    settlement_id: str,
    decision: DecisionState,
    difference_paise: int = 0,
    checks_failed: list[str] | None = None,
) -> ReconciliationResult:
    return ReconciliationResult(
        settlement_id=settlement_id,
        decision=decision,
        difference_paise=difference_paise,
        expected_amount_paise=100000,
        actual_amount_paise=100000 + difference_paise,
        deterministic_checks_passed=["schema_validation"],
        deterministic_checks_failed=checks_failed or [],
        escalate_to_human=decision != DecisionState.CLEAN_MATCH,
    )


def _make_gt(settlement_id: str, label: str) -> dict:
    return {"settlement_id": settlement_id, "label": label}


def _make_settlement(sid: str, created_at: str = "2026-08-20T10:00:00Z") -> dict:
    return {
        "settlement_id": sid,
        "amount": 100000,
        "created_at": created_at,
    }


# ---------------------------------------------------------------------------
# SYSTEMATIC_FEE_ROUNDING
# ---------------------------------------------------------------------------

class TestFeeRounding:
    def test_detects_identical_fee_discrepancy(self):
        results = [
            _make_result("S1", DecisionState.DETERMINISTIC_EXCEPTION, -5, ["fee_validation"]),
            _make_result("S2", DecisionState.DETERMINISTIC_EXCEPTION, -5, ["fee_validation"]),
            _make_result("S3", DecisionState.CLEAN_MATCH, 0),
        ]
        gt = [
            _make_gt("S1", "fee_mismatch"),
            _make_gt("S2", "fee_mismatch"),
            _make_gt("S3", "clean_match"),
        ]
        patterns = analyze_batch(results, gt)
        assert len(patterns) >= 1
        fee_pattern = next(p for p in patterns if p.pattern_type == PatternType.SYSTEMATIC_FEE_ROUNDING)
        assert "S1" in fee_pattern.affected_settlement_ids
        assert "S2" in fee_pattern.affected_settlement_ids
        assert fee_pattern.confidence > 0

    def test_single_fee_mismatch_no_pattern(self):
        results = [
            _make_result("S1", DecisionState.DETERMINISTIC_EXCEPTION, -5, ["fee_validation"]),
        ]
        gt = [_make_gt("S1", "fee_mismatch")]
        patterns = analyze_batch(results, gt)
        fee_patterns = [p for p in patterns if p.pattern_type == PatternType.SYSTEMATIC_FEE_ROUNDING]
        assert len(fee_patterns) == 0

    def test_different_fee_discrepancies_no_pattern(self):
        results = [
            _make_result("S1", DecisionState.DETERMINISTIC_EXCEPTION, -5, ["fee_validation"]),
            _make_result("S2", DecisionState.DETERMINISTIC_EXCEPTION, -10, ["fee_validation"]),
        ]
        gt = [
            _make_gt("S1", "fee_mismatch"),
            _make_gt("S2", "fee_mismatch"),
        ]
        patterns = analyze_batch(results, gt)
        fee_patterns = [p for p in patterns if p.pattern_type == PatternType.SYSTEMATIC_FEE_ROUNDING]
        assert len(fee_patterns) == 0


# ---------------------------------------------------------------------------
# REPEATED_BANK_DELAY
# ---------------------------------------------------------------------------

class TestBankDelay:
    def test_detects_consistent_overpayment(self):
        results = [
            _make_result("S1", DecisionState.MATH_DISCREPANCY, 5000),
            _make_result("S2", DecisionState.MATH_DISCREPANCY, 5000),
            _make_result("S3", DecisionState.MATH_DISCREPANCY, 5000),
        ]
        settlements = [
            _make_settlement("S1"),
            _make_settlement("S2"),
            _make_settlement("S3"),
        ]
        patterns = analyze_batch(results, settlements=settlements)
        delay_patterns = [p for p in patterns if p.pattern_type == PatternType.REPEATED_BANK_DELAY]
        assert len(delay_patterns) >= 1
        assert len(delay_patterns[0].affected_settlement_ids) == 3

    def test_too_few_no_pattern(self):
        results = [
            _make_result("S1", DecisionState.MATH_DISCREPANCY, 5000),
            _make_result("S2", DecisionState.MATH_DISCREPANCY, 5000),
        ]
        patterns = analyze_batch(results)
        delay_patterns = [p for p in patterns if p.pattern_type == PatternType.REPEATED_BANK_DELAY]
        assert len(delay_patterns) == 0


# ---------------------------------------------------------------------------
# REFUND_CLUSTER
# ---------------------------------------------------------------------------

class TestRefundCluster:
    def test_detects_same_date_refund_issues(self):
        results = [
            _make_result("S1", DecisionState.DETERMINISTIC_EXCEPTION, 100),
            _make_result("S2", DecisionState.DETERMINISTIC_EXCEPTION, 200),
        ]
        gt = [
            _make_gt("S1", "refund_timing"),
            _make_gt("S2", "refund_timing"),
        ]
        settlements = [
            _make_settlement("S1", "2026-08-20T10:00:00Z"),
            _make_settlement("S2", "2026-08-20T14:00:00Z"),
        ]
        patterns = analyze_batch(results, gt, settlements)
        refund_patterns = [p for p in patterns if p.pattern_type == PatternType.REFUND_CLUSTER]
        assert len(refund_patterns) >= 1
        assert "2026-08-20" in refund_patterns[0].description

    def test_different_dates_no_cluster(self):
        results = [
            _make_result("S1", DecisionState.DETERMINISTIC_EXCEPTION, 100),
            _make_result("S2", DecisionState.DETERMINISTIC_EXCEPTION, 200),
        ]
        gt = [
            _make_gt("S1", "refund_timing"),
            _make_gt("S2", "refund_timing"),
        ]
        settlements = [
            _make_settlement("S1", "2026-08-20T10:00:00Z"),
            _make_settlement("S2", "2026-08-25T14:00:00Z"),
        ]
        patterns = analyze_batch(results, gt, settlements)
        refund_patterns = [p for p in patterns if p.pattern_type == PatternType.REFUND_CLUSTER]
        assert len(refund_patterns) == 0


# ---------------------------------------------------------------------------
# REPEATED_UNEXPLAINED_GAP
# ---------------------------------------------------------------------------

class TestUnexplainedGap:
    def test_detects_similar_unexplained_gaps(self):
        results = [
            _make_result("S1", DecisionState.DETERMINISTIC_EXCEPTION, 1000),
            _make_result("S2", DecisionState.DETERMINISTIC_EXCEPTION, 1050),
            _make_result("S3", DecisionState.DETERMINISTIC_EXCEPTION, 980),
        ]
        gt = [
            _make_gt("S1", "unexplained"),
            _make_gt("S2", "unexplained"),
            _make_gt("S3", "unexplained"),
        ]
        patterns = analyze_batch(results, gt)
        gap_patterns = [p for p in patterns if p.pattern_type == PatternType.REPEATED_UNEXPLAINED_GAP]
        assert len(gap_patterns) >= 1
        assert len(gap_patterns[0].affected_settlement_ids) == 3

    def test_different_amounts_no_cluster(self):
        results = [
            _make_result("S1", DecisionState.DETERMINISTIC_EXCEPTION, 1000),
            _make_result("S2", DecisionState.DETERMINISTIC_EXCEPTION, 5000),
        ]
        gt = [
            _make_gt("S1", "unexplained"),
            _make_gt("S2", "unexplained"),
        ]
        patterns = analyze_batch(results, gt)
        gap_patterns = [p for p in patterns if p.pattern_type == PatternType.REPEATED_UNEXPLAINED_GAP]
        assert len(gap_patterns) == 0


# ---------------------------------------------------------------------------
# No patterns
# ---------------------------------------------------------------------------

class TestNoPatterns:
    def test_all_clean_returns_empty(self):
        results = [_make_result(f"S{i}", DecisionState.CLEAN_MATCH) for i in range(10)]
        gt = [_make_gt(f"S{i}", "clean_match") for i in range(10)]
        patterns = analyze_batch(results, gt)
        assert len(patterns) == 0

    def test_empty_batch_returns_empty(self):
        patterns = analyze_batch([], [])
        assert len(patterns) == 0


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

class TestFormatPatterns:
    def test_no_patterns_message(self):
        assert "No patterns detected" in format_patterns([])

    def test_formats_patterns(self):
        patterns = [
            DetectedPattern(
                pattern_type=PatternType.SYSTEMATIC_FEE_ROUNDING,
                affected_settlement_ids=["S1", "S2"],
                confidence=0.8,
                recommended_action="Review fee rule",
                description="2 settlements have same fee discrepancy.",
            ),
        ]
        report = format_patterns(patterns)
        assert "SYSTEMATIC_FEE_ROUNDING" in report
        assert "80%" in report
        assert "S1" in report
        assert "S2" in report


# ---------------------------------------------------------------------------
# Integration with generator
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_detects_pattern_on_synthetic_data(self):
        from backend.generator import generate_batch
        from backend.engine import run_engine

        data = generate_batch()
        results = run_engine(
            data["transactions"],
            data["settlements"],
            data["refunds"],
            data["bank_credits"],
        )
        patterns = analyze_batch(results, data["ground_truth"], data["settlements"])
        # Must detect at least one pattern on synthetic data
        assert len(patterns) >= 1, "Expected at least one pattern on synthetic data"

    def test_all_pattern_types_covered(self):
        """All 4 pattern types can be detected with appropriate data."""
        # SYSTEMATIC_FEE_ROUNDING
        results = [
            _make_result("S1", DecisionState.DETERMINISTIC_EXCEPTION, -5, ["fee_validation"]),
            _make_result("S2", DecisionState.DETERMINISTIC_EXCEPTION, -5, ["fee_validation"]),
        ]
        gt = [_make_gt("S1", "fee_mismatch"), _make_gt("S2", "fee_mismatch")]
        p = analyze_batch(results, gt)
        assert any(pat.pattern_type == PatternType.SYSTEMATIC_FEE_ROUNDING for pat in p)

        # REPEATED_UNEXPLAINED_GAP
        results = [
            _make_result("S3", DecisionState.DETERMINISTIC_EXCEPTION, 1000),
            _make_result("S4", DecisionState.DETERMINISTIC_EXCEPTION, 1020),
        ]
        gt = [_make_gt("S3", "unexplained"), _make_gt("S4", "unexplained")]
        p = analyze_batch(results, gt)
        assert any(pat.pattern_type == PatternType.REPEATED_UNEXPLAINED_GAP for pat in p)
