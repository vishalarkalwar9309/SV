"""Action model — records what the agent decided to do and what happened."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class AgentAction(BaseModel):
    """A decision by the agent to invoke a tool.

    This is the safe, externally-visible record of an agent step.
    It deliberately excludes internal chain-of-thought to prevent
    leaking private reasoning.
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:10])
    tool_name: str
    params: dict = Field(default_factory=dict)
    purpose: str = Field(description="Why the agent chose this action (safe summary, not raw CoT)")
    hypothesis_id: str | None = Field(
        default=None,
        description="The hypothesis this action is investigating",
    )
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ActionResult(BaseModel):
    """The outcome of executing an AgentAction.

    Captures both success and failure so the agent can adapt.
    """

    action_id: str
    success: bool
    data: dict | None = Field(default=None, description="Tool output on success")
    error: str | None = Field(default=None, description="Error message on failure")
    evidence_id: str | None = Field(
        default=None,
        description="ID of the EvidenceItem created from this result",
    )


class RemediationRecommendation(BaseModel):
    """A recommended remediation action, requiring human approval.

    The agent may propose a remediation, but it MUST NOT be executed
    without explicit human approval.
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:10])
    description: str
    action_type: str = Field(description="e.g. 'rollback', 'restart', 'config_change', 'scale_up'")
    target_service: str
    root_cause_hypothesis_id: str
    confidence: str = Field(description="'high', 'medium', or 'low'")
    approved: bool = False
    approved_by: str | None = None
    approved_at: datetime | None = None
