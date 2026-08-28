"""
Phase 8: Batch-Level AI Pattern Analysis

After individual settlements are processed, analyzes the entire batch for
cross-settlement patterns. Safe: does not approve anything. Surfaces
patterns humans miss.

Usage:
    from backend.batch_analyzer import analyze_batch
    patterns = analyze_batch(results, ground_truth, settlements)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.models import DecisionState, ReconciliationResult


# ---------------------------------------------------------------------------
# Pattern types (constrained per architecture)
# ---------------------------------------------------------------------------

class PatternType:
    SYSTEMATIC_FEE_ROUNDING = "SYSTEMATIC_FEE_ROUNDING"
    REPEATED_BANK_DELAY = "REPEATED_BANK_DELAY"
    REFUND_CLUSTER = "REFUND_CLUSTER"
    REPEATED_UNEXPLAINED_GAP = "REPEATED_UNEXPLAINED_GAP"


# ---------------------------------------------------------------------------
# Detected pattern
# ---------------------------------------------------------------------------

@dataclass
class DetectedPattern:
    """A cross-settlement pattern detected in the batch."""

    pattern_type: str
    affected_settlement_ids: list[str]
    confidence: float  # 0.0–1.0
    recommended_action: str
    description: str


# ---------------------------------------------------------------------------
# Pattern detectors
# ---------------------------------------------------------------------------

def _detect_fee_rounding(
    results: list[ReconciliationResult],
    ground_truth: list[dict[str, Any]],
) -> list[DetectedPattern]:
    """
    SYSTEMATIC_FEE_ROUNDING: same fee discrepancy across multiple settlements.
    Looks for multiple FEE_MISMATCH settlements with the same difference_paise.
    """
    gt_by_sid = {gt["settlement_id"]: gt for gt in ground_truth}

    # Group fee mismatches by their difference
    fee_diff_groups: dict[int, list[str]] = {}
    for r in results:
        gt = gt_by_sid.get(r.settlement_id)
        if gt and gt.get("label") == "fee_mismatch":
            diff = r.difference_paise
            if diff not in fee_diff_groups:
                fee_diff_groups[diff] = []
            fee_diff_groups[diff].append(r.settlement_id)

    patterns = []
    for diff, sids in fee_diff_groups.items():
        if len(sids) >= 2:
            confidence = min(1.0, len(sids) / 5.0)
            patterns.append(DetectedPattern(
                pattern_type=PatternType.SYSTEMATIC_FEE_ROUNDING,
                affected_settlement_ids=sids,
                confidence=confidence,
                recommended_action="Review fee rounding rule for consistency",
                description=(
                    f"{len(sids)} settlements have identical fee discrepancy of "
                    f"{diff} paise. Suggests systematic fee rounding issue."
                ),
            ))

    return patterns


def _detect_bank_delay(
    results: list[ReconciliationResult],
    settlements: list[dict[str, Any]],
) -> list[DetectedPattern]:
    """
    REPEATED_BANK_DELAY: same timing gap pattern across dates.
    Looks for multiple UNEXPLAINED or MATH_DISCREPANCY results where the
    difference is consistently positive (bank paid less than expected).
    """
    from collections import Counter

    # Find settlements with unexplained/math discrepancy
    diff_signs: list[str] = []
    diff_amounts: list[int] = []
    sids_by_sign: dict[str, list[str]] = {"positive": [], "negative": []}

    for r in results:
        if r.decision in {
            DecisionState.DETERMINISTIC_EXCEPTION,
            DecisionState.MATH_DISCREPANCY,
            DecisionState.REVIEW_REQUIRED,
            DecisionState.UNRESOLVED,
        }:
            checks = r.deterministic_checks_failed
            if r.decision == DecisionState.MATH_DISCREPANCY or not checks:
                if r.difference_paise > 0:
                    sids_by_sign["positive"].append(r.settlement_id)
                    diff_amounts.append(r.difference_paise)
                elif r.difference_paise < 0:
                    sids_by_sign["negative"].append(r.settlement_id)

    patterns = []
    for sign, sids in sids_by_sign.items():
        if len(sids) >= 3:
            confidence = min(1.0, len(sids) / 5.0)
            direction = "overpaid" if sign == "positive" else "underpaid"
            patterns.append(DetectedPattern(
                pattern_type=PatternType.REPEATED_BANK_DELAY,
                affected_settlement_ids=sids,
                confidence=confidence,
                recommended_action="Investigate bank settlement timing",
                description=(
                    f"{len(sids)} settlements show consistent bank {direction} pattern. "
                    f"May indicate systematic bank delay."
                ),
            ))

    return patterns


def _detect_refund_cluster(
    results: list[ReconciliationResult],
    ground_truth: list[dict[str, Any]],
    settlements: list[dict[str, Any]],
) -> list[DetectedPattern]:
    """
    REFUND_CLUSTER: multiple refund timing issues on same date.
    Looks for multiple REFUND_TIMING settlements created on the same date.
    """
    gt_by_sid = {gt["settlement_id"]: gt for gt in ground_truth}
    settlement_by_sid = {s["settlement_id"]: s for s in settlements}

    # Group refund timing issues by date
    date_groups: dict[str, list[str]] = {}
    for r in results:
        gt = gt_by_sid.get(r.settlement_id)
        if gt and gt.get("label") == "refund_timing":
            s = settlement_by_sid.get(r.settlement_id)
            if s:
                # Use created_at date (first 10 chars of YYYY-MM-DD)
                created = s.get("created_at", "")[:10]
                if created not in date_groups:
                    date_groups[created] = []
                date_groups[created].append(r.settlement_id)

    patterns = []
    for date, sids in date_groups.items():
        if len(sids) >= 2:
            confidence = min(1.0, len(sids) / 3.0)
            patterns.append(DetectedPattern(
                pattern_type=PatternType.REFUND_CLUSTER,
                affected_settlement_ids=sids,
                confidence=confidence,
                recommended_action="Review refund processing timing on " + date,
                description=(
                    f"{len(sids)} refund timing issues on {date}. "
                    f"May indicate batch refund processing delay."
                ),
            ))

    return patterns


def _detect_unexplained_gaps(
    results: list[ReconciliationResult],
    ground_truth: list[dict[str, Any]],
) -> list[DetectedPattern]:
    """
    REPEATED_UNEXPLAINED_GAP: multiple unexplained gaps with similar amounts.
    Looks for multiple UNEXPLAINED results with similar difference_paise.
    """
    gt_by_sid = {gt["settlement_id"]: gt for gt in ground_truth}

    # Group unexplained by approximate difference (within 10%)
    unexplained: list[tuple[str, int]] = []
    for r in results:
        gt = gt_by_sid.get(r.settlement_id)
        if gt and gt.get("label") == "unexplained":
            unexplained.append((r.settlement_id, r.difference_paise))

    if len(unexplained) < 2:
        return []

    # Cluster by similarity (within 10% of each other)
    patterns = []
    used: set[str] = set()
    for i, (sid1, diff1) in enumerate(unexplained):
        if sid1 in used:
            continue
        cluster_sids = [sid1]
        cluster_diffs = [diff1]
        for j, (sid2, diff2) in enumerate(unexplained):
            if i != j and sid2 not in used:
                if diff1 != 0 and abs(diff1 - diff2) / abs(diff1) <= 0.1:
                    cluster_sids.append(sid2)
                    cluster_diffs.append(diff2)
                    used.add(sid2)
        if len(cluster_sids) >= 2:
            used.update(cluster_sids)
            avg_diff = sum(cluster_diffs) // len(cluster_diffs)
            confidence = min(1.0, len(cluster_sids) / 4.0)
            patterns.append(DetectedPattern(
                pattern_type=PatternType.REPEATED_UNEXPLAINED_GAP,
                affected_settlement_ids=cluster_sids,
                confidence=confidence,
                recommended_action="Investigate common cause for similar gaps",
                description=(
                    f"{len(cluster_sids)} settlements have unexplained gaps "
                    f"of ~{avg_diff} paise. May share a common cause."
                ),
            ))

    return patterns


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------

def analyze_batch(
    results: list[ReconciliationResult],
    ground_truth: list[dict[str, Any]] | None = None,
    settlements: list[dict[str, Any]] | None = None,
) -> list[DetectedPattern]:
    """
    Analyze a batch of reconciliation results for cross-settlement patterns.

    Args:
        results: Engine reconciliation results, one per settlement.
        ground_truth: Optional ground-truth labels for pattern classification.
        settlements: Optional settlement dicts for date-based analysis.

    Returns:
        List of detected patterns, sorted by confidence descending.
    """
    if ground_truth is None:
        ground_truth = []
    if settlements is None:
        settlements = []

    patterns: list[DetectedPattern] = []
    patterns.extend(_detect_fee_rounding(results, ground_truth))
    patterns.extend(_detect_bank_delay(results, settlements))
    patterns.extend(_detect_refund_cluster(results, ground_truth, settlements))
    patterns.extend(_detect_unexplained_gaps(results, ground_truth))

    # Sort by confidence descending
    patterns.sort(key=lambda p: p.confidence, reverse=True)
    return patterns


def format_patterns(patterns: list[DetectedPattern]) -> str:
    """Format detected patterns into a readable report."""
    if not patterns:
        return "No patterns detected in this batch."

    lines = [f"Detected {len(patterns)} pattern(s):"]
    for i, p in enumerate(patterns, 1):
        lines.append(f"\n{i}. {p.pattern_type} (confidence: {p.confidence:.0%})")
        lines.append(f"   {p.description}")
        lines.append(f"   Affected: {', '.join(p.affected_settlement_ids)}")
        lines.append(f"   Recommendation: {p.recommended_action}")
    return "\n".join(lines)
