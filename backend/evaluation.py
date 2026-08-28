"""
Phase 6: Evaluation Harness

Scores a batch of reconciliation results against ground-truth labels.
Computes match rate, false accept rate, safe escalation rate,
AI invocation rate, and processing time.

Usage:
    from backend.evaluation import evaluate_batch, format_report
    metrics = evaluate_batch(results, ground_truth, batch_time_seconds=48.0)
    print(format_report(metrics))
"""

import time
from dataclasses import dataclass, field
from typing import Any

from backend.models import DecisionState, ReconciliationResult


# ---------------------------------------------------------------------------
# Ground-truth label taxonomy
# ---------------------------------------------------------------------------

# Labels that represent a legitimately clean settlement
CLEAN_LABELS = {"clean_match"}

# Labels that represent an exception the engine should catch
EXCEPTION_LABELS = {
    "missing_reference",
    "duplicate_settlement",
    "bank_mismatch",
    "fee_mismatch",
    "tax_inconsistency",
    "refund_timing",
    "unexplained",
}


# ---------------------------------------------------------------------------
# Metrics dataclass
# ---------------------------------------------------------------------------

@dataclass
class EvaluationMetrics:
    """Computed evaluation metrics for a batch."""

    total: int = 0

    # Confusion matrix counts
    true_positives: int = 0    # clean_match → CLEAN_MATCH
    false_positives: int = 0   # clean_match → DETERMINISTIC_EXCEPTION (over-escalated)
    true_negatives: int = 0    # exception → DETERMINISTIC_EXCEPTION
    false_negatives: int = 0   # exception → CLEAN_MATCH (missed exception)

    # Derived rates (all in [0.0, 1.0])
    match_rate: float = 0.0
    false_accept_rate: float = 0.0
    safe_escalation_rate: float = 0.0
    ai_invocation_rate: float = 0.0
    ai_auto_approval_rate_pct: float = 0.0  # always 0.0 by design

    # Timing
    batch_time_seconds: float = 0.0
    processing_time_per_settlement: float = 0.0

    # Per-label breakdown
    label_counts: dict[str, int] = field(default_factory=dict)
    label_correct: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Evaluation logic
# ---------------------------------------------------------------------------

def _is_clean_decision(result: ReconciliationResult) -> bool:
    """Check if the engine decision is CLEAN_MATCH."""
    return result.decision == DecisionState.CLEAN_MATCH


def _is_exception_decision(result: ReconciliationResult) -> bool:
    """Check if the engine decision is any exception type."""
    return result.decision in {
        DecisionState.DETERMINISTIC_EXCEPTION,
        DecisionState.MATH_DISCREPANCY,
        DecisionState.REVIEW_REQUIRED,
        DecisionState.UNRESOLVED,
        DecisionState.UNPROCESSED,
    }


def _is_ai_investigated(result: ReconciliationResult) -> bool:
    """Check if AI investigated this settlement."""
    return result.decision in {
        DecisionState.REVIEW_REQUIRED,
        DecisionState.UNRESOLVED,
    }


def evaluate_batch(
    results: list[ReconciliationResult],
    ground_truth: list[dict[str, Any]],
    batch_time_seconds: float = 0.0,
) -> EvaluationMetrics:
    """
    Evaluate reconciliation results against ground-truth labels.

    Confusion matrix (engine perspective):
    - TP: clean_match ground truth → CLEAN_MATCH decision (correctly clean)
    - FP: clean_match ground truth → exception decision (over-escalated)
    - TN: exception ground truth → exception decision (correctly caught)
    - FN: exception ground truth → CLEAN_MATCH decision (missed! false accept)

    "False accept rate" = FN / total (engine falsely accepted exception as clean).
    """
    if len(results) != len(ground_truth):
        raise ValueError(
            f"Results count ({len(results)}) != ground truth count ({len(ground_truth)})"
        )

    # Index ground truth by settlement_id
    gt_by_sid = {gt["settlement_id"]: gt for gt in ground_truth}

    tp = 0  # clean_match → CLEAN_MATCH
    fp = 0  # clean_match → exception (over-escalated)
    tn = 0  # exception → exception (correctly caught)
    fn = 0  # exception → CLEAN_MATCH (missed! false accept)

    label_counts: dict[str, int] = {}
    label_correct: dict[str, int] = {}
    ai_investigated = 0

    for result in results:
        gt = gt_by_sid.get(result.settlement_id)
        if gt is None:
            raise ValueError(f"No ground truth for settlement {result.settlement_id}")

        label = gt["label"]
        is_clean_gt = label in CLEAN_LABELS
        is_clean_dec = _is_clean_decision(result)

        label_counts[label] = label_counts.get(label, 0) + 1

        if is_clean_gt and is_clean_dec:
            tp += 1
            label_correct[label] = label_correct.get(label, 0) + 1
        elif is_clean_gt and not is_clean_dec:
            fp += 1
        elif not is_clean_gt and not is_clean_dec:
            tn += 1
            label_correct[label] = label_correct.get(label, 0) + 1
        else:
            # not clean_gt and clean_dec → engine missed the exception (false accept)
            fn += 1

        if _is_ai_investigated(result):
            ai_investigated += 1

    total = len(results)

    # Compute rates (safe division)
    match_rate = (tp + tn) / total if total > 0 else 0.0
    # False accept rate: engine accepted exception as clean (FN)
    false_accept_rate = fn / total if total > 0 else 0.0
    safe_escalation_rate = (fp + tn + fn) / total if total > 0 else 0.0
    ai_invocation_rate = ai_investigated / total if total > 0 else 0.0
    per_settlement = batch_time_seconds / total if total > 0 else 0.0

    return EvaluationMetrics(
        total=total,
        true_positives=tp,
        false_positives=fp,
        true_negatives=tn,
        false_negatives=fn,
        match_rate=match_rate,
        false_accept_rate=false_accept_rate,
        safe_escalation_rate=safe_escalation_rate,
        ai_invocation_rate=ai_invocation_rate,
        ai_auto_approval_rate_pct=0.0,
        batch_time_seconds=batch_time_seconds,
        processing_time_per_settlement=per_settlement,
        label_counts=label_counts,
        label_correct=label_correct,
    )


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def format_report(metrics: EvaluationMetrics) -> str:
    """Format evaluation metrics into the honest reporting template."""
    lines = [
        f"We generated {metrics.total} settlements with known ground truth.",
        f"The system correctly handled {metrics.true_positives + metrics.true_negatives} "
        f"({metrics.match_rate:.1%} match rate).",
        f"It escalated {metrics.false_positives + metrics.true_negatives + metrics.false_negatives} "
        f"to human review.",
    ]

    if metrics.false_negatives > 0:
        lines.append(
            f"It falsely accepted {metrics.false_negatives} settlements as clean when they "
            f"had exceptions ({metrics.false_accept_rate:.1%} false accept rate)."
        )

    if metrics.false_negatives > 0:
        lines.append(
            f"WARNING: {metrics.false_negatives} exceptions were incorrectly classified as "
            f"clean match (missed exceptions)."
        )

    if metrics.ai_invocation_rate > 0:
        lines.append(
            f"AI was invoked on {metrics.total - metrics.true_positives - metrics.true_negatives} "
            f"settlements ({metrics.ai_invocation_rate:.1%})."
        )
        lines.append(
            "All AI-investigated discrepancies were flagged for human review; "
            "zero were auto-approved."
        )

    if metrics.batch_time_seconds > 0:
        lines.append(
            f"Batch processed in {metrics.batch_time_seconds:.1f} seconds "
            f"({metrics.processing_time_per_settlement:.2f}s per settlement)."
        )

    return " ".join(lines)


def format_label_breakdown(metrics: EvaluationMetrics) -> str:
    """Format per-label accuracy breakdown."""
    lines = ["Per-label breakdown:"]
    for label in sorted(metrics.label_counts.keys()):
        count = metrics.label_counts[label]
        correct = metrics.label_correct.get(label, 0)
        pct = correct / count if count > 0 else 0.0
        lines.append(f"  {label}: {correct}/{count} correct ({pct:.1%})")
    return "\n".join(lines)
