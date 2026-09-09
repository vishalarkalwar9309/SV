"""Incident model — the entry point for every investigation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class Severity(StrEnum):
    """Incident severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Incident(BaseModel):
    """An incident that triggers an investigation.

    Represents the initial alert or observation that something is wrong.
    The agent uses this as the starting point for root-cause analysis.
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    title: str
    service: str
    severity: Severity
    initial_observation: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Optional: the ground-truth root cause (set by the simulator, never shown to the agent)
    ground_truth_root_cause: str | None = Field(
        default=None,
        description=(
            "Known root cause from the simulator. "
            "Used for evaluation only, never exposed to the agent."
        ),
    )
