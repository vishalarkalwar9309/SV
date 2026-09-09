"""Agent controller — orchestrates the boundary between planning and deterministic execution."""

from __future__ import annotations

from trace.agent.planner import BasePlanner, PlannedAction
from trace.agent.registry import ToolRegistry
from trace.engine.decision_trace import DecisionTraceStep
from trace.engine.evaluator import EvaluationRelationship, EvaluationResult, EvidenceEvaluator
from trace.engine.evidence_manager import EvidenceManager
from trace.engine.hypothesis_manager import HypothesisManager
from trace.engine.state import InvestigationState, InvestigationStatus
from trace.models.action import ActionResult, AgentAction
from trace.models.evidence import EvidenceItem
from trace.models.hypothesis import HypothesisStatus

from pydantic import BaseModel, ConfigDict


class ControllerError(Exception):
    """Base error for agent controller operations."""


class ActionValidationError(ControllerError, ValueError):
    """Raised when a planner-produced action fails validation."""


class StepLimitExceededError(ControllerError, RuntimeError):
    """Raised when investigation exceeds the maximum allowed action steps."""


class ControllerStepResult(BaseModel):
    """The outcome of executing a single controller step."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    action: AgentAction
    result: ActionResult
    evidence: EvidenceItem
    evaluation: EvaluationResult | None = None
    is_terminal: bool = False


class AgentController:
    """Orchestrates the agentic boundary: Planner → Action → Tool → Evidence → Evaluator.

    Guarantees:
    - Never executes unvalidated or malformed actions
    - Never fabricates tool data or evidence
    - Delegates all evidence evaluation to the deterministic EvidenceEvaluator
    - Enforces safety limits (max steps, no execution after resolution)
    - Records safe decision traces without chain-of-thought
    """

    def __init__(
        self,
        planner: BasePlanner,
        tool_registry: ToolRegistry,
        evaluator: EvidenceEvaluator | None = None,
        hypothesis_manager: HypothesisManager | None = None,
        evidence_manager: EvidenceManager | None = None,
        max_steps: int = 10,
    ) -> None:
        self.planner = planner
        self.tool_registry = tool_registry
        self.evaluator = evaluator or EvidenceEvaluator()
        self.evidence_manager = evidence_manager or EvidenceManager()
        self.hypothesis_manager = hypothesis_manager or HypothesisManager(
            evidence_store=self.evidence_manager.store
        )
        self.max_steps = max_steps

    def validate_action(
        self,
        action: PlannedAction,
        state: InvestigationState,
    ) -> None:
        """Validate a planner-produced action against safety rules.

        Raises ActionValidationError if any check fails.
        """
        # 1. Action must have a non-empty purpose
        if not action.purpose or not action.purpose.strip():
            raise ActionValidationError("Planned action must specify a non-empty purpose")

        # 2. Tool must exist in the registry
        tool = self.tool_registry.get(action.tool_name)
        if tool is None:
            raise ActionValidationError(
                f"Unknown tool '{action.tool_name}'. Tool is not registered."
            )

        # 3. Parameters must supply all required fields specified by the tool schema
        schema = tool.get_parameters_schema()
        required_fields = schema.get("required", [])
        missing_fields = [f for f in required_fields if f not in action.parameters]
        if missing_fields:
            raise ActionValidationError(
                f"Missing required parameter(s) {missing_fields} for tool '{action.tool_name}'"
            )

        # 4. If target hypothesis_id is supplied, it must exist in the state or hypothesis manager
        if action.hypothesis_id is not None:
            exists_in_state = any(h.id == action.hypothesis_id for h in state.hypotheses)
            exists_in_mgr = self.hypothesis_manager.get(action.hypothesis_id) is not None
            if not exists_in_state and not exists_in_mgr:
                raise ActionValidationError(
                    f"Target hypothesis ID '{action.hypothesis_id}' does not exist."
                )

    def step(self, state: InvestigationState) -> ControllerStepResult | None:
        """Execute a single investigation step.

        Returns ControllerStepResult if a step was executed, or None if the investigation
        has finished or cannot proceed.
        """
        # Safety stopping conditions
        if state.investigation_status in (
            InvestigationStatus.RESOLVED,
            InvestigationStatus.EXHAUSTED,
            InvestigationStatus.BLOCKED,
        ):
            return None

        if len(state.actions_taken) >= self.max_steps:
            state.investigation_status = InvestigationStatus.EXHAUSTED
            return None

        # 1. Propose action via Planner
        available_tools = self.tool_registry.get_metadata()
        planned_action = self.planner.plan_next_action(state, available_tools)
        if planned_action is None:
            return None

        # 2. Validate action strictly
        self.validate_action(planned_action, state)

        # 3. Convert to AgentAction and record in state
        agent_action = planned_action.to_agent_action()
        state.actions_taken.append(agent_action)

        # 4. Resolve and execute tool safely
        tool = self.tool_registry.get_or_raise(planned_action.tool_name)
        action_result = tool.execute(**planned_action.parameters)
        action_result.action_id = agent_action.id

        # 5. Record deterministic evidence with provenance
        evidence_item = self.evidence_manager.record_tool_result(
            source_tool=tool.name,
            query=planned_action.parameters,
            success=action_result.success,
            raw_data=action_result.data,
            error_message=action_result.error,
        )
        action_result.evidence_id = evidence_item.id
        state.evidence.append(evidence_item)

        # 6. Delegate evaluation to EvidenceEvaluator
        eval_result: EvaluationResult | None = None
        hypothesis = None
        if planned_action.hypothesis_id:
            hypothesis = self.hypothesis_manager.get(planned_action.hypothesis_id)
            if hypothesis is None:
                hypothesis = state.get_hypothesis(planned_action.hypothesis_id)
                if hypothesis:
                    self.hypothesis_manager.register(hypothesis)

            if hypothesis:
                # Active action transitions a proposed hypothesis to investigating
                if hypothesis.status == HypothesisStatus.PROPOSED:
                    self.hypothesis_manager.update_status(
                        hypothesis.id, HypothesisStatus.INVESTIGATING
                    )

                eval_result = self.evaluator.evaluate(hypothesis, evidence_item)

                if eval_result.relationship == EvaluationRelationship.SUPPORTS:
                    self.hypothesis_manager.attach_supporting_evidence(
                        hypothesis.id, evidence_item.id
                    )
                elif eval_result.relationship == EvaluationRelationship.CONTRADICTS:
                    self.hypothesis_manager.attach_contradicting_evidence(
                        hypothesis.id, evidence_item.id
                    )

                # Aggregate evaluations for hypothesis
                evals = [eval_result]
                summary = self.evaluator.aggregate(hypothesis.id, evals)

                # Conservative status update
                if summary.recommended_status in (
                    HypothesisStatus.DISPROVEN,
                    HypothesisStatus.SUPPORTED,
                ):
                    try:
                        self.hypothesis_manager.update_status(
                            hypothesis.id,
                            summary.recommended_status,
                            disproval_reason=eval_result.reason
                            if summary.recommended_status == HypothesisStatus.DISPROVEN
                            else None,
                        )
                    except Exception:
                        pass

                if hypothesis.status == HypothesisStatus.SUPPORTED:
                    state.investigation_status = InvestigationStatus.RESOLVED

        # 7. Record decision trace step (audit trail)
        obs_text = self._format_observation(action_result)
        trace_step = DecisionTraceStep(
            step_number=len(state.decision_trace) + 1,
            action=tool.name,
            purpose=planned_action.purpose,
            observation=obs_text,
            evaluation=f"{eval_result.relationship.value.upper()} ({eval_result.score_delta})"
            if eval_result
            else None,
            hypothesis_status=hypothesis.status.value if hypothesis else None,
            next_action="Continue investigation"
            if state.investigation_status == InvestigationStatus.ACTIVE
            else f"Investigation {state.investigation_status.value}",
        )
        state.decision_trace.append(trace_step)

        return ControllerStepResult(
            action=agent_action,
            result=action_result,
            evidence=evidence_item,
            evaluation=eval_result,
            is_terminal=state.investigation_status == InvestigationStatus.RESOLVED,
        )

    def run(self, state: InvestigationState, max_steps: int | None = None) -> InvestigationState:
        """Run the controller loop up to max_steps or until terminal state."""
        limit = max_steps if max_steps is not None else self.max_steps
        steps_taken = 0

        while steps_taken < limit:
            step_result = self.step(state)
            if step_result is None:
                break
            steps_taken += 1
            if step_result.is_terminal:
                break

        if (
            state.investigation_status == InvestigationStatus.ACTIVE
            and len(state.actions_taken) >= limit
        ):
            state.investigation_status = InvestigationStatus.EXHAUSTED

        return state

    @staticmethod
    def _format_observation(result: ActionResult) -> str:
        if not result.success:
            return f"Tool execution failed: {result.error}"
        data = result.data or {}
        if "metrics" in data:
            metrics = data["metrics"]
            cpu = metrics.get("cpu_percent", "N/A")
            mem = metrics.get("memory_percent", "N/A")
            return f"CPU: {cpu}%, Memory: {mem}%"
        if "matches" in data:
            count = data.get("count", len(data["matches"]))
            return f"Found {count} log match(es)"
        if "commits" in data:
            count = data.get("count", len(data["commits"]))
            return f"Retrieved {count} commit(s)"
        return "Tool completed successfully"
