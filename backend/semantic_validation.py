"""
Semantic validation layer: second LLM validates first LLM's classification.

Catches hallucinated classifications, inconsistent reasoning, and
low-confidence predictions that should be escalated.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("nivara.semantic")


class SemanticValidator:
    """Validates AI classifications for consistency and correctness."""

    VALID_CLASSIFICATIONS = {
        "CLEAN_MATCH", "TIMING_MISMATCH", "REFUND_TIMING",
        "FEE_DISCREPANCY", "UNEXPLAINED",
    }

    VALID_ACTIONS = {"AUTO_RESOLVE", "ESCALATE_TO_HUMAN"}

    def validate(
        self,
        classification: str,
        explanation: str,
        confidence: float,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate a classification against semantic rules."""
        issues = []

        # 1. Classification must be valid
        if classification not in self.VALID_CLASSIFICATIONS:
            issues.append(f"Invalid classification: {classification}")

        # 2. Confidence bounds
        if not (0.0 <= confidence <= 1.0):
            issues.append(f"Confidence out of bounds: {confidence}")

        # 3. CLEAN_MATCH should have high confidence
        if classification == "CLEAN_MATCH" and confidence < 0.8:
            issues.append("CLEAN_MATCH with low confidence — possible false positive")

        # 4. UNEXPLAINED should not have confidence > 0.9
        if classification == "UNEXPLAINED" and confidence > 0.9:
            issues.append("UNEXPLAINED with suspiciously high confidence")

        # 5. Explanation should be non-empty and meaningful
        if len(explanation.strip()) < 10:
            issues.append("Explanation too short")

        # 6. Evidence must cite at least one source
        cited = evidence.get("cited_evidence", [])
        if not cited:
            issues.append("No evidence cited")

        # 7. Cross-check: if TIMING_MISMATCH, timing evidence should exist
        if classification == "TIMING_MISMATCH":
            timing = evidence.get("timing", {})
            if not timing:
                issues.append("TIMING_MISMATCH without timing evidence")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "recommendation": "ESCALATE_TO_HUMAN" if issues else None,
        }


# Global instance
_validator: Optional[SemanticValidator] = None


def get_validator() -> SemanticValidator:
    global _validator
    if _validator is None:
        _validator = SemanticValidator()
    return _validator


def validate_classification(
    classification: str,
    explanation: str,
    confidence: float,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Convenience function to validate a classification."""
    return get_validator().validate(classification, explanation, confidence, evidence)
