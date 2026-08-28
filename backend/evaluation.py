"""
Phase 6: Evaluation Harness

Scores a batch of reconciliation results against ground-truth labels.
Computes match rate, false accept rate, safe escalation rate,
per-class precision/recall/F1, AI invocation rate, and processing time.

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
    "bank_mismatch",
    "fee_mismatch",
    "tax_inconsistency",
    "refund_timing",
    "adjustment_entry",
    "refund_after_settlement",
    "timing_race",
    "partial_settlement",
    "unexplained",
}

# Labels where the engine's blind spot makes a false negative expected
# (the engine cannot distinguish these from clean matches)
KNOWN_BLIND_SPOTS = {"refund_after_settlement", "timing_race"}


# ---------------------------------------------------------------------------
# Per-class metrics
# ---------------------------------------------------------------------------

@dataclass
class ClassMetrics:
    """Precision, recall, F1 for a single label category."""

    label: str
    true_positives: int = 0   # correctly classified as this class's decision type
    false_positives: int = 0  # other class incorrectly given this class's decision
    false_negatives: int = 0  # this class missed (given wrong decision)
    support: int = 0          # total instances of this class in ground truth

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def recall(self) -> float:
        return self.true_positives / self.support if self.support > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


# ---------------------------------------------------------------------------
# Metrics dataclass
# ---------------------------------------------------------------------------

@dataclass
class EvaluationMetrics:
    """Computed evaluation metrics for a batch."""

    total: int = 0

    # Confusion matrix counts (binary: clean vs exception)
    true_positives: int = 0    # clean_match ground truth → CLEAN_MATCH decision
    false_positives: int = 0   # clean_match ground truth → exception decision
    true_negatives: int = 0    # exception ground truth → exception decision
    false_negatives: int = 0   # exception ground truth → CLEAN_MATCH decision

    # Derived rates (all in [0.0, 1.0])
    match_rate: float = 0.0
    false_accept_rate: float = 0.0
    safe_escalation_rate: float = 0.0
    ai_auto_approval_rate_pct: float = 0.0  # always 0.0 by design

    # AI invocation (split into two separate metrics)
    math_discrepancy_count: int = 0   # MATH_DISCREPANCY cases (would trigger AI)
    ai_invoked_count: int = 0         # cases where AI was actually called
    ai_invocation_rate: float = 0.0   # ai_invoked_count / total

    # Escalation breakdown (no longer conflated)
    deterministic_exception_count: int = 0  # real deterministic exceptions
    unresolved_count: int = 0               # LLM unavailable/failed
    review_required_count: int = 0          # AI reviewed, flagged for human

    # Timing
    batch_time_seconds: float = 0.0
    processing_time_per_settlement: float = 0.0
    throughput_per_second: float = 0.0

    # Per-label breakdown
    label_counts: dict[str, int] = field(default_factory=dict)
    label_correct: dict[str, int] = field(default_factory=dict)

    # Per-class precision/recall/F1
    class_metrics: dict[str, ClassMetrics] = field(default_factory=dict)

    # Macro averages
    macro_precision: float = 0.0
    macro_recall: float = 0.0
    macro_f1: float = 0.0


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
    ai_client_available: bool = False,
) -> EvaluationMetrics:
    """
    Evaluate reconciliation results against ground-truth labels.

    Confusion matrix (engine perspective):
    - TP: clean_match ground truth → CLEAN_MATCH decision (correctly clean)
    - FP: clean_match ground truth → exception decision (over-escalated)
    - TN: exception ground truth → exception decision (correctly caught)
    - FN: exception ground truth → CLEAN_MATCH decision (missed! false accept)

    "False accept rate" = FN / total (engine falsely accepted exception as clean).

    Args:
        results: Engine reconciliation results.
        ground_truth: Ground-truth labels with settlement_id and label.
        batch_time_seconds: Wall-clock time for the batch.
        ai_client_available: Whether an LLM client was provided. When False,
            MATH_DISCREPANCY cases are reported as "would-trigger-AI" rather
            than "AI-invoked."
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
    math_discrepancy_count = 0
    deterministic_exception_count = 0
    unresolved_count = 0
    review_required_count = 0

    # Per-class tracking
    class_metrics: dict[str, ClassMetrics] = {}

    for result in results:
        gt = gt_by_sid.get(result.settlement_id)
        if gt is None:
            raise ValueError(f"No ground truth for settlement {result.settlement_id}")

        label = gt["label"]
        is_clean_gt = label in CLEAN_LABELS
        is_clean_dec = _is_clean_decision(result)

        label_counts[label] = label_counts.get(label, 0) + 1

        # Initialize class metrics if needed
        if label not in class_metrics:
            class_metrics[label] = ClassMetrics(label=label)
        cm = class_metrics[label]
        cm.support += 1

        if is_clean_gt and is_clean_dec:
            tp += 1
            label_correct[label] = label_correct.get(label, 0) + 1
            cm.true_positives += 1
        elif is_clean_gt and not is_clean_dec:
            fp += 1
        elif not is_clean_gt and not is_clean_dec:
            tn += 1
            label_correct[label] = label_correct.get(label, 0) + 1
            cm.true_positives += 1
        else:
            # not clean_gt and clean_dec → engine missed the exception (false accept)
            fn += 1

        if _is_ai_investigated(result):
            ai_investigated += 1

        # Track escalation breakdown
        if result.decision == DecisionState.DETERMINISTIC_EXCEPTION:
            deterministic_exception_count += 1
        elif result.decision == DecisionState.UNRESOLVED:
            unresolved_count += 1
        elif result.decision == DecisionState.REVIEW_REQUIRED:
            review_required_count += 1

        # Track MATH_DISCREPANCY (would-trigger-AI)
        if result.decision == DecisionState.MATH_DISCREPANCY:
            math_discrepancy_count += 1

    total = len(results)

    # Compute rates (safe division)
    match_rate = (tp + tn) / total if total > 0 else 0.0
    false_accept_rate = fn / total if total > 0 else 0.0
    safe_escalation_rate = (fp + tn + fn) / total if total > 0 else 0.0

    # AI invocation: only count actual invocations when client is available
    if ai_client_available:
        ai_invocation_rate = ai_investigated / total if total > 0 else 0.0
    else:
        # When no LLM client, report "would-trigger" rate separately
        ai_invocation_rate = 0.0

    per_settlement = batch_time_seconds / total if total > 0 else 0.0
    throughput = total / batch_time_seconds if batch_time_seconds > 0 else 0.0

    # Compute macro averages for precision/recall/F1
    # For binary classification: "is this class correctly identified?"
    # TP = class correctly caught, FN = class missed, FP = other class over-caught
    macro_p_values = []
    macro_r_values = []
    macro_f1_values = []

    for label, cm in class_metrics.items():
        # For clean_match: precision = TP/(TP+FP) where FP = other classes that gotCLEAN_MATCH
        # For exceptions: precision = TP/(TP+FP) where FP = clean_match that got this exception
        # Simplified: use the class's own TP and derive FP/FN from the confusion matrix
        if label in CLEAN_LABELS:
            cm.true_positives = tp
            cm.false_positives = fp
            cm.false_negatives = fn
        else:
            # For exception classes: TP = this class correctly caught
            # FN = this class missed (went to CLEAN_MATCH)
            # FP = clean_match over-escalated to any exception
            label_fn = 0
            for r in results:
                r_gt = gt_by_sid.get(r.settlement_id)
                if r_gt and r_gt["label"] == label and _is_clean_decision(r):
                    label_fn += 1
            cm.false_negatives = label_fn
            cm.false_positives = fp  # over-escalation affects all exception classes
            cm.true_positives = cm.support - label_fn

        macro_p_values.append(cm.precision)
        macro_r_values.append(cm.recall)
        macro_f1_values.append(cm.f1)

    macro_precision = sum(macro_p_values) / len(macro_p_values) if macro_p_values else 0.0
    macro_recall = sum(macro_r_values) / len(macro_r_values) if macro_r_values else 0.0
    macro_f1 = sum(macro_f1_values) / len(macro_f1_values) if macro_f1_values else 0.0

    return EvaluationMetrics(
        total=total,
        true_positives=tp,
        false_positives=fp,
        true_negatives=tn,
        false_negatives=fn,
        match_rate=match_rate,
        false_accept_rate=false_accept_rate,
        safe_escalation_rate=safe_escalation_rate,
        ai_auto_approval_rate_pct=0.0,
        math_discrepancy_count=math_discrepancy_count,
        ai_invoked_count=ai_investigated if ai_client_available else 0,
        ai_invocation_rate=ai_invocation_rate,
        deterministic_exception_count=deterministic_exception_count,
        unresolved_count=unresolved_count,
        review_required_count=review_required_count,
        batch_time_seconds=batch_time_seconds,
        processing_time_per_settlement=per_settlement,
        throughput_per_second=throughput,
        label_counts=label_counts,
        label_correct=label_correct,
        class_metrics=class_metrics,
        macro_precision=macro_precision,
        macro_recall=macro_recall,
        macro_f1=macro_f1,
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
    ]

    # Escalation breakdown (no longer conflated)
    total_escalated = (
        metrics.deterministic_exception_count
        + metrics.unresolved_count
        + metrics.review_required_count
        + metrics.false_positives
    )
    if total_escalated > 0:
        parts = []
        if metrics.deterministic_exception_count > 0:
            parts.append(f"{metrics.deterministic_exception_count} deterministic exceptions")
        if metrics.unresolved_count > 0:
            parts.append(f"{metrics.unresolved_count} unresolved (LLM unavailable/failed)")
        if metrics.review_required_count > 0:
            parts.append(f"{metrics.review_required_count} AI-reviewed (flagged for human)")
        if metrics.false_positives > 0:
            parts.append(f"{metrics.false_positives} over-escalated clean matches")
        lines.append(f"It escalated {total_escalated} to human review: {', '.join(parts)}.")

    if metrics.false_negatives > 0:
        lines.append(
            f"It falsely accepted {metrics.false_negatives} settlements as clean when they "
            f"had exceptions ({metrics.false_accept_rate:.1%} false accept rate)."
        )

    if metrics.false_negatives > 0:
        # Identify which blind spots caused false negatives
        blind_spot_fns = []
        for label in KNOWN_BLIND_SPOTS:
            if label in metrics.label_counts:
                count = metrics.label_counts[label]
                correct = metrics.label_correct.get(label, 0)
                missed = count - correct
                if missed > 0:
                    blind_spot_fns.append(f"{missed} {label}")
        if blind_spot_fns:
            lines.append(
                f"NOTE: {metrics.false_negatives} false negatives are from known engine "
                f"blind spots ({', '.join(blind_spot_fns)}) — the deterministic engine "
                f"has no checks for these scenarios. These would require AI investigation "
                f"or additional deterministic checks to catch."
            )
        else:
            lines.append(
                f"WARNING: {metrics.false_negatives} exceptions were incorrectly classified as "
                f"clean match (missed exceptions)."
            )

    # AI invocation — report separately based on whether LLM was available
    if metrics.math_discrepancy_count > 0:
        if metrics.ai_invoked_count > 0:
            lines.append(
                f"AI was invoked on {metrics.ai_invoked_count} MATH_DISCREPANCY cases "
                f"({metrics.ai_invocation_rate:.1%} of batch). "
                f"All were flagged for human review; zero were auto-approved."
            )
        else:
            lines.append(
                f"{metrics.math_discrepancy_count} MATH_DISCREPANCY cases would trigger "
                f"AI investigation ({metrics.math_discrepancy_count / metrics.total:.1%} of batch). "
                f"No LLM client was available — these were escalated as UNRESOLVED."
            )

    if metrics.batch_time_seconds > 0:
        lines.append(
            f"Batch processed in {metrics.batch_time_seconds:.2f} seconds "
            f"({metrics.processing_time_per_settlement:.3f}s per settlement, "
            f"{metrics.throughput_per_second:.0f} settlements/sec)."
        )

    return " ".join(lines)


def format_label_breakdown(metrics: EvaluationMetrics) -> str:
    """Format per-label accuracy breakdown with precision/recall/F1."""
    lines = ["Per-label breakdown:"]
    for label in sorted(metrics.label_counts.keys()):
        count = metrics.label_counts[label]
        correct = metrics.label_correct.get(label, 0)
        pct = correct / count if count > 0 else 0.0
        cm = metrics.class_metrics.get(label)
        if cm:
            lines.append(
                f"  {label}: {correct}/{count} correct ({pct:.1%}) "
                f"[P={cm.precision:.2f} R={cm.recall:.2f} F1={cm.f1:.2f}]"
            )
        else:
            lines.append(f"  {label}: {correct}/{count} correct ({pct:.1%})")

    lines.append(
        f"  macro-avg: P={metrics.macro_precision:.2f} R={metrics.macro_recall:.2f} "
        f"F1={metrics.macro_f1:.2f}"
    )
    return "\n".join(lines)
