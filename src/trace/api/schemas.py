"""API / View schemas for TRACE 2.0.

Provides explicit, safe presentation schemas separated from internal domain models.
Guarantees:
- ground_truth_root_cause is never exposed
- No fabricated hypothesis confidence scores
- Raw evidence preservation without invented semantics
- Safe, audit-only decision trace without chain-of-thought or secrets
"""

from __future__ import annotations

from datetime import datetime
from trace.engine.decision_trace import DecisionTraceStep
from trace.engine.state import InvestigationState
from trace.models.action import AgentAction
from trace.models.evidence import EvidenceItem
from trace.models.hypothesis import Hypothesis
from trace.models.incident import Incident
from typing import Any

from pydantic import BaseModel, Field


class IncidentResponse(BaseModel):
    """View model for an incident alert. Excludes ground_truth_root_cause."""

    id: str
    title: str
    service: str
    severity: str
    initial_observation: str
    timestamp: datetime

    @classmethod
    def from_incident(cls, incident: Incident) -> IncidentResponse:
        return cls(
            id=incident.id,
            title=incident.title,
            service=incident.service,
            severity=incident.severity.value,
            initial_observation=incident.initial_observation,
            timestamp=incident.timestamp,
        )


class HypothesisResponse(BaseModel):
    """View model for an incident hypothesis. Excludes fabricated confidence scores."""

    id: str
    statement: str
    status: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    disproval_reason: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_hypothesis(cls, hypothesis: Hypothesis) -> HypothesisResponse:
        return cls(
            id=hypothesis.id,
            statement=hypothesis.statement,
            status=hypothesis.status.value,
            supporting_evidence_ids=list(hypothesis.supporting_evidence_ids),
            contradicting_evidence_ids=list(hypothesis.contradicting_evidence_ids),
            disproval_reason=hypothesis.disproval_reason,
            created_at=hypothesis.created_at,
            updated_at=hypothesis.updated_at,
        )


class EvidenceResponse(BaseModel):
    """View model for raw tool evidence. Excludes invented semantic fields."""

    id: str
    source_tool: str
    query: dict[str, Any] = Field(default_factory=dict)
    raw_data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime
    tool_succeeded: bool
    error_message: str | None = None

    @classmethod
    def from_evidence(cls, evidence: EvidenceItem) -> EvidenceResponse:
        return cls(
            id=evidence.id,
            source_tool=evidence.source_tool,
            query=dict(evidence.query),
            raw_data=dict(evidence.raw_data),
            timestamp=evidence.timestamp,
            tool_succeeded=evidence.tool_succeeded,
            error_message=evidence.error_message,
        )


class ActionResponse(BaseModel):
    """View model for an executed investigation action."""

    id: str
    tool_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    purpose: str
    hypothesis_id: str | None = None
    timestamp: datetime

    @classmethod
    def from_action(cls, action: AgentAction) -> ActionResponse:
        return cls(
            id=action.id,
            tool_name=action.tool_name,
            params=dict(action.params),
            purpose=action.purpose,
            hypothesis_id=action.hypothesis_id,
            timestamp=action.timestamp,
        )


class DecisionTraceResponse(BaseModel):
    """Safe audit trail step. Excludes internal chain-of-thought, thoughts, and secrets."""

    step_number: int
    action: str
    purpose: str
    observation: str
    evaluation: str | None = None
    hypothesis_status: str | None = None
    next_action: str | None = None

    @classmethod
    def from_trace_step(cls, step: DecisionTraceStep) -> DecisionTraceResponse:
        return cls(
            step_number=step.step_number,
            action=step.action,
            purpose=step.purpose,
            observation=step.observation,
            evaluation=step.evaluation,
            hypothesis_status=step.hypothesis_status,
            next_action=step.next_action,
        )


class InvestigationResponse(BaseModel):
    """View model for the overall investigation state snapshot."""

    investigation_id: str
    incident: IncidentResponse
    status: str
    hypotheses: list[HypothesisResponse] = Field(default_factory=list)
    evidence: list[EvidenceResponse] = Field(default_factory=list)
    actions_taken: list[ActionResponse] = Field(default_factory=list)
    decision_trace: list[DecisionTraceResponse] = Field(default_factory=list)

    @classmethod
    def from_state(
        cls, investigation_id: str, state: InvestigationState
    ) -> InvestigationResponse:
        return cls(
            investigation_id=investigation_id,
            incident=IncidentResponse.from_incident(state.incident),
            status=state.investigation_status.value,
            hypotheses=[HypothesisResponse.from_hypothesis(h) for h in state.hypotheses],
            evidence=[EvidenceResponse.from_evidence(e) for e in state.evidence],
            actions_taken=[ActionResponse.from_action(a) for a in state.actions_taken],
            decision_trace=[
                DecisionTraceResponse.from_trace_step(s) for s in state.decision_trace
            ],
        )


class CreateInvestigationRequest(BaseModel):
    """Request payload to initiate an investigation."""

    incident_id: str = Field(min_length=1, description="Identifier of the incident to investigate")
