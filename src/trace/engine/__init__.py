"""TRACE Engine — deterministic hypothesis, evidence, and state management."""

from __future__ import annotations

from trace.engine.baseline import (
    DeterministicBaselineResult,
    run_deterministic_baseline,
)
from trace.engine.decision_trace import DecisionTraceStep
from trace.engine.evaluator import (
    EvaluationRelationship,
    EvaluationResult,
    EvidenceEvaluator,
    EvidenceWeight,
    HypothesisEvaluationSummary,
)
from trace.engine.evidence_manager import (
    DuplicateEvidenceError,
    EvidenceError,
    EvidenceManager,
    EvidenceNotFoundError,
    InvalidEvidenceClassificationError,
)
from trace.engine.hypothesis_manager import (
    VALID_TRANSITIONS,
    DuplicateHypothesisError,
    HypothesisError,
    HypothesisManager,
    HypothesisNotFoundError,
    InvalidTransitionError,
)
from trace.engine.state import InvestigationState, InvestigationStatus

__all__ = [
    "DecisionTraceStep",
    "DeterministicBaselineResult",
    "DuplicateEvidenceError",
    "DuplicateHypothesisError",
    "EvaluationRelationship",
    "EvaluationResult",
    "EvidenceError",
    "EvidenceEvaluator",
    "EvidenceManager",
    "EvidenceNotFoundError",
    "EvidenceWeight",
    "HypothesisError",
    "HypothesisEvaluationSummary",
    "HypothesisManager",
    "HypothesisNotFoundError",
    "InvalidEvidenceClassificationError",
    "InvalidTransitionError",
    "InvestigationState",
    "InvestigationStatus",
    "VALID_TRANSITIONS",
    "run_deterministic_baseline",
]
