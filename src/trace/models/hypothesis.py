"""Hypothesis model — the core unit of reasoning in TRACE."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class HypothesisStatus(StrEnum):
    """Lifecycle status of a hypothesis.

    PROPOSED    → just generated, no evidence yet
    INVESTIGATING → actively being tested
    SUPPORTED   → evidence supports this as root cause
    DISPROVEN   → evidence contradicts this
    INSUFFICIENT → some evidence gathered, but not enough to decide
    """

    PROPOSED = "proposed"
    INVESTIGATING = "investigating"
    SUPPORTED = "supported"
    DISPROVEN = "disproven"
    INSUFFICIENT = "insufficient"


class Hypothesis(BaseModel):
    """A candidate explanation for the incident.

    The agent generates hypotheses, the deterministic evaluator
    decides their status based on collected evidence. The agent
    must NOT set a hypothesis to SUPPORTED or DISPROVEN — only
    the evaluator may do that.
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    statement: str
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    disproval_reason: str | None = None

    def is_terminal(self) -> bool:
        """Whether this hypothesis has reached a final state."""
        return self.status in (HypothesisStatus.SUPPORTED, HypothesisStatus.DISPROVEN)
