"""Investigation state — snapshot of the ongoing incident analysis."""

from __future__ import annotations

from enum import StrEnum
from trace.models.action import AgentAction
from trace.models.evidence import EvidenceItem
from trace.models.hypothesis import Hypothesis
from trace.models.incident import Incident

from pydantic import BaseModel, Field


class InvestigationStatus(StrEnum):
    """Lifecycle status of the overall investigation."""

    NOT_STARTED = "not_started"
    ACTIVE = "active"
    RESOLVED = "resolved"  # root cause identified and verified
    EXHAUSTED = "exhausted"  # all hypotheses investigated without clear conclusion
    BLOCKED = "blocked"  # necessary tools/data unavailable


class InvestigationState(BaseModel):
    """Deterministic, serializable state snapshot of an incident investigation.

    Holds the incident context, competing hypotheses, collected evidence items,
    and action history. Independent instances maintain completely isolated state.
    """

    incident: Incident
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    actions_taken: list[AgentAction] = Field(default_factory=list)
    current_hypothesis_id: str | None = None
    investigation_status: InvestigationStatus = InvestigationStatus.NOT_STARTED

    @property
    def current_hypothesis(self) -> Hypothesis | None:
        """Return the currently focused Hypothesis, or None."""
        if not self.current_hypothesis_id:
            return None
        for h in self.hypotheses:
            if h.id == self.current_hypothesis_id:
                return h
        return None

    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis | None:
        """Find a hypothesis by ID in the current state."""
        for h in self.hypotheses:
            if h.id == hypothesis_id:
                return h
        return None

    def get_evidence(self, evidence_id: str) -> EvidenceItem | None:
        """Find an evidence item by ID in the current state."""
        for item in self.evidence:
            if item.id == evidence_id:
                return item
        return None
