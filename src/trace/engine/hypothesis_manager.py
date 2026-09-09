"""Hypothesis manager — deterministic state tracking for incident hypotheses."""

from __future__ import annotations

from datetime import UTC, datetime
from trace.engine.evidence_manager import (
    EvidenceManager,
)
from trace.models.evidence import EvidenceStore
from trace.models.hypothesis import Hypothesis, HypothesisStatus


class HypothesisError(Exception):
    """Base error for hypothesis operations."""


class DuplicateHypothesisError(HypothesisError, ValueError):
    """Raised when registering a hypothesis with an ID that already exists."""


class HypothesisNotFoundError(HypothesisError, KeyError):
    """Raised when a hypothesis is not found."""


class InvalidTransitionError(HypothesisError, ValueError):
    """Raised when an illegal hypothesis status transition is attempted."""


# Explicit deterministic state transition rules
VALID_TRANSITIONS: dict[HypothesisStatus, set[HypothesisStatus]] = {
    HypothesisStatus.PROPOSED: {
        HypothesisStatus.INVESTIGATING,
    },
    HypothesisStatus.INVESTIGATING: {
        HypothesisStatus.SUPPORTED,
        HypothesisStatus.DISPROVEN,
        HypothesisStatus.INSUFFICIENT,
    },
    HypothesisStatus.INSUFFICIENT: {
        HypothesisStatus.INVESTIGATING,
    },
    HypothesisStatus.SUPPORTED: set(),  # terminal state
    HypothesisStatus.DISPROVEN: set(),  # terminal state
}


class HypothesisManager:
    """Deterministic manager for hypotheses and their lifecycle transitions.

    NOTE: The Hypothesis Manager does NOT decide whether evidence supports or contradicts
    a hypothesis — that is M1-04's Evaluator responsibility. The manager strictly
    records state supplied by future orchestration/evaluator code and enforces valid
    lifecycle transitions.
    """

    def __init__(
        self,
        evidence_store: EvidenceStore | EvidenceManager | None = None,
    ) -> None:
        self._hypotheses: dict[str, Hypothesis] = {}
        if isinstance(evidence_store, EvidenceStore):
            self._evidence_manager: EvidenceManager | None = EvidenceManager(evidence_store)
        elif isinstance(evidence_store, EvidenceManager):
            self._evidence_manager = evidence_store
        else:
            self._evidence_manager = None

    def create(
        self,
        statement: str,
        hypothesis_id: str | None = None,
    ) -> Hypothesis:
        """Create and register a new hypothesis in PROPOSED status."""
        kwargs: dict = {"statement": statement}
        if hypothesis_id is not None:
            kwargs["id"] = hypothesis_id
        hypothesis = Hypothesis(**kwargs)
        return self.register(hypothesis)

    def register(self, hypothesis: Hypothesis) -> Hypothesis:
        """Register an existing hypothesis instance. Prevents duplicate IDs."""
        if hypothesis.id in self._hypotheses:
            raise DuplicateHypothesisError(
                f"Hypothesis with ID '{hypothesis.id}' already exists"
            )
        self._hypotheses[hypothesis.id] = hypothesis
        return hypothesis

    def get(self, hypothesis_id: str) -> Hypothesis | None:
        """Retrieve a hypothesis by ID, or None if not found."""
        return self._hypotheses.get(hypothesis_id)

    def get_or_raise(self, hypothesis_id: str) -> Hypothesis:
        """Retrieve a hypothesis by ID, or raise HypothesisNotFoundError."""
        hypothesis = self.get(hypothesis_id)
        if hypothesis is None:
            raise HypothesisNotFoundError(f"Hypothesis '{hypothesis_id}' not found")
        return hypothesis

    def list_all(self, status: HypothesisStatus | None = None) -> list[Hypothesis]:
        """List all hypotheses, optionally filtered by status."""
        if status is None:
            return list(self._hypotheses.values())
        return [h for h in self._hypotheses.values() if h.status == status]

    def update_status(
        self,
        hypothesis_id: str,
        new_status: HypothesisStatus,
        disproval_reason: str | None = None,
    ) -> Hypothesis:
        """Transition a hypothesis to a new status following deterministic rules.

        Raises InvalidTransitionError if the transition is illegal or if the hypothesis
        is already in a terminal state (SUPPORTED / DISPROVEN).
        """
        hypothesis = self.get_or_raise(hypothesis_id)
        current_status = hypothesis.status

        allowed = VALID_TRANSITIONS.get(current_status, set())
        if new_status not in allowed:
            if hypothesis.is_terminal():
                raise InvalidTransitionError(
                    f"Cannot transition hypothesis '{hypothesis_id}' from terminal state "
                    f"'{current_status}' to '{new_status}'."
                )
            raise InvalidTransitionError(
                f"Illegal transition for hypothesis '{hypothesis_id}': "
                f"'{current_status}' -> '{new_status}'. Allowed: {[s.value for s in allowed]}"
            )

        hypothesis.status = new_status
        hypothesis.updated_at = datetime.now(UTC)

        if new_status == HypothesisStatus.DISPROVEN and disproval_reason:
            hypothesis.disproval_reason = disproval_reason

        return hypothesis

    def attach_supporting_evidence(
        self,
        hypothesis_id: str,
        evidence_id: str,
    ) -> Hypothesis:
        """Attach an evidence ID as supporting evidence for a hypothesis.

        If an evidence manager was provided, validates that the evidence is eligible
        (e.g., rejecting failed tool calls).
        """
        hypothesis = self.get_or_raise(hypothesis_id)
        if self._evidence_manager is not None:
            self._evidence_manager.validate_root_cause_eligibility(evidence_id)

        if evidence_id not in hypothesis.supporting_evidence_ids:
            hypothesis.supporting_evidence_ids.append(evidence_id)
            hypothesis.updated_at = datetime.now(UTC)

        return hypothesis

    def attach_contradicting_evidence(
        self,
        hypothesis_id: str,
        evidence_id: str,
    ) -> Hypothesis:
        """Attach an evidence ID as contradicting evidence for a hypothesis.

        If an evidence manager was provided, validates that the evidence is eligible
        (e.g., rejecting failed tool calls).
        """
        hypothesis = self.get_or_raise(hypothesis_id)
        if self._evidence_manager is not None:
            self._evidence_manager.validate_root_cause_eligibility(evidence_id)

        if evidence_id not in hypothesis.contradicting_evidence_ids:
            hypothesis.contradicting_evidence_ids.append(evidence_id)
            hypothesis.updated_at = datetime.now(UTC)

        return hypothesis

    def get_active(self) -> list[Hypothesis]:
        """Return all non-terminal hypotheses (PROPOSED, INVESTIGATING, INSUFFICIENT)."""
        return [h for h in self._hypotheses.values() if not h.is_terminal()]

    def get_supported(self) -> list[Hypothesis]:
        """Return all hypotheses with status SUPPORTED (root-cause candidates)."""
        return [h for h in self._hypotheses.values() if h.status == HypothesisStatus.SUPPORTED]

    def get_root_cause_candidates(self) -> list[Hypothesis]:
        """Alias for get_supported()."""
        return self.get_supported()

    def get_disproven(self) -> list[Hypothesis]:
        """Return all disproven hypotheses. Clearly distinguished from active/supported."""
        return [h for h in self._hypotheses.values() if h.status == HypothesisStatus.DISPROVEN]
