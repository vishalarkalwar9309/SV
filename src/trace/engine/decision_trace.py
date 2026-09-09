"""Decision trace — structured audit record of the deterministic investigation sequence."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DecisionTraceStep(BaseModel):
    """A single structured step in the investigation audit trail.

    Captures the deterministic progression from action to observation,
    evaluation, hypothesis state update, and next action.
    Excludes internal chain-of-thought reasoning.
    """

    step_number: int
    action: str = Field(description="Tool or action executed")
    purpose: str = Field(description="Goal of this action")
    observation: str = Field(description="Observable outcome from tool data")
    evaluation: str | None = Field(
        default=None,
        description="Deterministic evaluation result (e.g. 'CONTRADICTS (-3)')",
    )
    hypothesis_status: str | None = Field(
        default=None,
        description="Hypothesis lifecycle status after this step",
    )
    next_action: str | None = Field(
        default=None,
        description="Next deterministic pivot or concluding action",
    )
