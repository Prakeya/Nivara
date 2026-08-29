"""
Phase 8: Deterministic Batch-Level Pattern Analysis

After individual settlements are processed, analyzes the entire batch for
cross-settlement patterns using deterministic rules. Does NOT use AI/LLM.
Safe: does not approve anything. Surfaces patterns humans miss.

This module is intentionally deterministic — pattern detection across
settlements must be reproducible and auditable. AI-assisted pattern
detection is handled separately in the agent layer.

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
    ground_truth: list[dict[str, Any]] | None = None,
) -> list[DetectedPattern]:
    """
    SYSTEMATIC_FEE_ROUNDING: same fee discrepancy across multiple settlements.
    Detects MATH_DISCREPANCY results where fee validation passed but there is
    a consistent difference (indicating systematic rounding).
    """
    fee_diff_groups: dict[int, list[str]] = {}
    for r in results:
        if (r.decision == DecisionState.MATH_DISCREPANCY
                and "fee_validation" in r.deterministic_checks_passed
                and r.difference_paise != 0):
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
                    f"{len(sids)} settlements have identical discrepancy of "
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
    ground_truth: list[dict[str, Any]] | None = None,
    settlements: list[dict[str, Any]] | None = None,
) -> list[DetectedPattern]:
    """
    REFUND_CLUSTER: multiple settlements with refund-related failures.
    Detects DETERMINISTIC_EXCEPTION results where refund_overage or
    refund-related checks failed.
    """
    if settlements is None:
        settlements = []
    settlement_by_sid = {s["settlement_id"]: s for s in settlements}

    refund_failures: list[str] = []
    for r in results:
        if (r.decision == DecisionState.DETERMINISTIC_EXCEPTION
                and any("refund" in fc.lower() for fc in r.deterministic_checks_failed)):
            refund_failures.append(r.settlement_id)

    if len(refund_failures) < 2:
        return []

    confidence = min(1.0, len(refund_failures) / 3.0)
    return [DetectedPattern(
        pattern_type=PatternType.REFUND_CLUSTER,
        affected_settlement_ids=refund_failures,
        confidence=confidence,
        recommended_action="Review refund processing for affected settlements",
        description=(
            f"{len(refund_failures)} settlements have refund-related failures. "
            f"May indicate batch refund processing issues."
        ),
    )]


def _detect_unexplained_gaps(
    results: list[ReconciliationResult],
    ground_truth: list[dict[str, Any]] | None = None,
) -> list[DetectedPattern]:
    """
    REPEATED_UNEXPLAINED_GAP: multiple MATH_DISCREPANCY results with similar
    difference amounts.
    """
    gaps: list[tuple[str, int]] = []
    for r in results:
        if r.decision == DecisionState.MATH_DISCREPANCY and r.difference_paise != 0:
            gaps.append((r.settlement_id, r.difference_paise))

    if len(gaps) < 2:
        return []

    patterns = []
    used: set[str] = set()
    for i, (sid1, diff1) in enumerate(gaps):
        if sid1 in used:
            continue
        cluster_sids = [sid1]
        cluster_diffs = [diff1]
        for j, (sid2, diff2) in enumerate(gaps):
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
                    f"{len(cluster_sids)} settlements have gaps "
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

    Detects patterns from deterministic engine outcomes only. ground_truth
    and settlements are accepted for backward compatibility but are not
    used for pattern detection (production safety invariant).

    Args:
        results: Engine reconciliation results, one per settlement.
        ground_truth: Ignored. Accepted for API compatibility.
        settlements: Optional settlement dicts for date-based analysis.

    Returns:
        List of detected patterns, sorted by confidence descending.
    """
    if settlements is None:
        settlements = []

    patterns: list[DetectedPattern] = []
    patterns.extend(_detect_fee_rounding(results))
    patterns.extend(_detect_bank_delay(results, settlements))
    patterns.extend(_detect_refund_cluster(results, settlements=settlements))
    patterns.extend(_detect_unexplained_gaps(results))

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
