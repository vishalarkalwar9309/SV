"""Autonomous adaptive investigation loop for TRACE 2.0.

Orchestrates repeated planning-execution-evaluation cycles:
Planner.plan_next_action(state) → Controller.step(state) → Updated state → repeat
Enforces strict stopping conditions, step budgets, and safety limits.
"""

from __future__ import annotations

from trace.agent.controller import ActionValidationError, AgentController
from trace.agent.llm_planner import PlannerError
from trace.engine.decision_trace import DecisionTraceStep
from trace.engine.state import InvestigationState, InvestigationStatus


class AutonomousInvestigationLoop:
    """Bounded autonomous adaptive investigation loop.

    Connects a BasePlanner with an AgentController to enable dynamic re-planning
    after every observation and evaluation.
    """

    def __init__(
        self,
        controller: AgentController,
        max_steps: int = 10,
        max_consecutive_duplicates: int = 3,
    ) -> None:
        self.controller = controller
        self.max_steps = max_steps
        self.max_consecutive_duplicates = max_consecutive_duplicates

    def run(
        self,
        state: InvestigationState,
        max_steps: int | None = None,
    ) -> InvestigationState:
        """Run the adaptive investigation loop until a terminal condition is met.

        Stopping Conditions:
        1. state.investigation_status == RESOLVED
        2. state.investigation_status == EXHAUSTED
        3. state.investigation_status == BLOCKED
        4. Planner returns None (no further action proposed)
        5. Step budget (max_steps) reached
        6. Action validation or planner error encountered
        7. Max consecutive duplicate actions detected
        """
        budget = max_steps if max_steps is not None else self.max_steps
        self.controller.max_steps = budget

        # 1. Guard against running on already terminal state
        if state.investigation_status in (
            InvestigationStatus.RESOLVED,
            InvestigationStatus.EXHAUSTED,
            InvestigationStatus.BLOCKED,
        ):
            return state

        if state.investigation_status == InvestigationStatus.NOT_STARTED:
            state.investigation_status = InvestigationStatus.ACTIVE

        consecutive_duplicate_count = 0
        last_action_signature: tuple | None = None

        while len(state.actions_taken) < budget:
            # Check terminal condition before each step
            if state.investigation_status in (
                InvestigationStatus.RESOLVED,
                InvestigationStatus.EXHAUSTED,
                InvestigationStatus.BLOCKED,
            ):
                break

            # Execute one controller step with error isolation
            try:
                step_result = self.controller.step(state)
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
                break
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
                break

            # Planner returned None or no further step could be taken
            if step_result is None:
                if state.investigation_status == InvestigationStatus.ACTIVE:
                    state.investigation_status = InvestigationStatus.EXHAUSTED
                break

            # Duplicate action loop detection
            current_signature = (
                step_result.action.tool_name,
                tuple(sorted(step_result.action.params.items())),
                step_result.action.hypothesis_id,
            )
            if current_signature == last_action_signature:
                consecutive_duplicate_count += 1
                if consecutive_duplicate_count >= self.max_consecutive_duplicates:
                    state.investigation_status = InvestigationStatus.BLOCKED
                    break
            else:
                consecutive_duplicate_count = 1
                last_action_signature = current_signature

            # Stop if step reached resolution
            if (
                step_result.is_terminal
                or state.investigation_status == InvestigationStatus.RESOLVED
            ):
                break

        # If budget exhausted while still active
        if (
            state.investigation_status == InvestigationStatus.ACTIVE
            and len(state.actions_taken) >= budget
        ):
            state.investigation_status = InvestigationStatus.EXHAUSTED

        return state
