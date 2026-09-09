"""Application service for TRACE 2.0 investigations.

Coordinates between the API layer and the underlying agent/simulator domain.
Owns:
- Incident lookup and listing
- Fresh InvestigationState initialization
- In-memory InvestigationStore management
- Single-step controller execution orchestration
- Conversion to API/view models
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from trace.agent.controller import ActionValidationError, AgentController
from trace.agent.llm_planner import PlannerError
from trace.agent.planner import BasePlanner
from trace.agent.registry import ToolRegistry
from trace.api.schemas import IncidentResponse, InvestigationResponse
from trace.engine.decision_trace import DecisionTraceStep
from trace.engine.state import InvestigationState, InvestigationStatus
from trace.models.hypothesis import Hypothesis
from trace.simulator.models import ScenarioData
from trace.simulator.scenarios import checkout_502
from trace.tools.fetch_commits import FetchRecentCommitsTool
from trace.tools.grep_logs import GrepLogsTool
from trace.tools.query_metrics import QueryMetricsTool
from uuid import uuid4


class ApplicationError(Exception):
    """Base application layer exception."""


class IncidentNotFoundError(ApplicationError):
    """Raised when an incident ID cannot be resolved in the simulator."""


class InvestigationNotFoundError(ApplicationError):
    """Raised when an investigation ID does not exist in the store."""


# Deterministic scenario catalog
AVAILABLE_SCENARIOS: dict[str, Callable[[], ScenarioData]] = {
    "checkout_502": checkout_502.create,
}


def normalize_incident_id(incident_id: str) -> str:
    """Normalize incident identifiers to handle hyphen or underscore variants."""
    return incident_id.strip().replace("-", "_").lower()


@dataclass
class InvestigationSession:
    """In-memory session holding state, controller, and scenario for an investigation."""

    investigation_id: str
    state: InvestigationState
    controller: AgentController
    scenario: ScenarioData


class InvestigationStore:
    """Minimal in-memory store for ongoing investigation sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, InvestigationSession] = {}

    def save(self, session: InvestigationSession) -> None:
        self._sessions[session.investigation_id] = session

    def get(self, investigation_id: str) -> InvestigationSession | None:
        return self._sessions.get(investigation_id)

    def list_all(self) -> list[InvestigationSession]:
        return list(self._sessions.values())

    def clear(self) -> None:
        self._sessions.clear()


class InvestigationService:
    """Application service coordinating investigation lifecycle and execution."""

    def __init__(
        self,
        store: InvestigationStore | None = None,
        planner: BasePlanner | None = None,
        planner_factory: Callable[[], BasePlanner] | None = None,
    ) -> None:
        self.store = store if store is not None else InvestigationStore()
        self._planner = planner
        self._planner_factory = planner_factory

    def _resolve_planner(self) -> BasePlanner:
        """Resolve planner instance, defaulting lazily to LLMPlanner."""
        if self._planner is not None:
            return self._planner
        if self._planner_factory is not None:
            return self._planner_factory()
        # Default runtime planner: imported lazily to avoid network/import overhead
        from trace.agent.llm_planner import LLMPlanner

        return LLMPlanner()

    def get_all_incidents(self) -> list[IncidentResponse]:
        """Return all available deterministic simulator incidents as view models."""
        incidents: list[IncidentResponse] = []
        for factory in AVAILABLE_SCENARIOS.values():
            scenario = factory()
            incidents.append(IncidentResponse.from_incident(scenario.incident))
        return incidents

    def get_incident(self, incident_id: str) -> IncidentResponse:
        """Look up a single incident by ID. Raises IncidentNotFoundError if missing."""
        norm_id = normalize_incident_id(incident_id)
        factory = AVAILABLE_SCENARIOS.get(norm_id)
        if factory is None:
            raise IncidentNotFoundError(f"Incident '{incident_id}' not found")
        scenario = factory()
        return IncidentResponse.from_incident(scenario.incident)

    def create_investigation(self, incident_id: str) -> InvestigationResponse:
        """Create a fresh investigation for the specified incident without executing steps."""
        norm_id = normalize_incident_id(incident_id)
        factory = AVAILABLE_SCENARIOS.get(norm_id)
        if factory is None:
            raise IncidentNotFoundError(f"Incident '{incident_id}' not found")

        scenario = factory()
        tools = [
            QueryMetricsTool(scenario),
            GrepLogsTool(scenario),
            FetchRecentCommitsTool(scenario),
        ]
        registry = ToolRegistry(tools)
        planner = self._resolve_planner()
        controller = AgentController(planner=planner, tool_registry=registry)

        # Seed initial hypotheses for primary scenario
        initial_hypotheses: list[Hypothesis] = []
        if norm_id == "checkout_502":
            initial_hypotheses = [
                Hypothesis(id="h1-db", statement="Database overload"),
                Hypothesis(id="h2-leak", statement="Checkout connection leak"),
            ]

        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=initial_hypotheses,
            investigation_status=InvestigationStatus.NOT_STARTED,
        )

        investigation_id = f"inv-{uuid4().hex[:8]}"
        session = InvestigationSession(
            investigation_id=investigation_id,
            state=state,
            controller=controller,
            scenario=scenario,
        )
        self.store.save(session)

        return InvestigationResponse.from_state(investigation_id, state)

    def get_investigation(self, investigation_id: str) -> InvestigationResponse:
        """Retrieve the current state of an investigation. Raises InvestigationNotFoundError."""
        session = self.store.get(investigation_id)
        if session is None:
            raise InvestigationNotFoundError(f"Investigation '{investigation_id}' not found")
        return InvestigationResponse.from_state(investigation_id, session.state)

    def step(self, investigation_id: str) -> InvestigationResponse:
        """Execute exactly one investigation step and return the updated state."""
        session = self.store.get(investigation_id)
        if session is None:
            raise InvestigationNotFoundError(f"Investigation '{investigation_id}' not found")

        state = session.state
        controller = session.controller

        # Respect terminal state protection
        if state.investigation_status in (
            InvestigationStatus.RESOLVED,
            InvestigationStatus.EXHAUSTED,
            InvestigationStatus.BLOCKED,
        ):
            return InvestigationResponse.from_state(investigation_id, state)

        # Transition NOT_STARTED to ACTIVE on first execution
        if state.investigation_status == InvestigationStatus.NOT_STARTED:
            state.investigation_status = InvestigationStatus.ACTIVE

        # Execute single controller step with safety isolation
        try:
            step_result = controller.step(state)
        except ActionValidationError as err:
            trace_step = DecisionTraceStep(
                step_number=len(state.decision_trace) + 1,
                action="validation_failure",
                purpose="Action validation",
                observation=f"Action validation rejected: {err}",
                evaluation="NEUTRAL (0)",
                hypothesis_status=state.current_hypothesis.status.value
                if state.current_hypothesis
                else None,
                next_action="Halt investigation due to invalid planner action",
            )
            state.decision_trace.append(trace_step)
            state.investigation_status = InvestigationStatus.BLOCKED
            step_result = None
        except PlannerError as err:
            trace_step = DecisionTraceStep(
                step_number=len(state.decision_trace) + 1,
                action="planner_failure",
                purpose="Action planning",
                observation=f"Planner error: {err}",
                evaluation="NEUTRAL (0)",
                hypothesis_status=state.current_hypothesis.status.value
                if state.current_hypothesis
                else None,
                next_action="Halt investigation due to planner failure",
            )
            state.decision_trace.append(trace_step)
            state.investigation_status = InvestigationStatus.BLOCKED
            step_result = None

        if step_result is None and state.investigation_status == InvestigationStatus.ACTIVE:
            state.investigation_status = InvestigationStatus.EXHAUSTED

        return InvestigationResponse.from_state(investigation_id, state)
