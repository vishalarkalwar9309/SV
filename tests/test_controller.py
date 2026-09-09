"""Deterministic tests for AgentController, ToolRegistry, and Planner contract."""

from trace.agent.controller import (
    ActionValidationError,
    AgentController,
    ControllerStepResult,
)
from trace.agent.planner import MockPlanner, PlannedAction
from trace.agent.registry import (
    DuplicateToolError,
    ToolNotFoundError,
    ToolRegistry,
)
from trace.engine.evaluator import EvaluationRelationship
from trace.engine.state import InvestigationState, InvestigationStatus
from trace.models.hypothesis import Hypothesis, HypothesisStatus
from trace.simulator.models import ToolFailureConfig
from trace.simulator.scenarios.checkout_502 import create as create_checkout_scenario
from trace.tools.fetch_commits import FetchRecentCommitsTool
from trace.tools.grep_logs import GrepLogsTool
from trace.tools.query_metrics import QueryMetricsTool

import pytest


class TestPlannerContract:
    def test_planned_action_creation(self):
        plan = PlannedAction(
            tool_name="query_metrics",
            parameters={"service": "checkout-db"},
            purpose="Check CPU usage",
            hypothesis_id="h-01",
        )
        assert plan.tool_name == "query_metrics"
        assert plan.parameters["service"] == "checkout-db"
        assert plan.purpose == "Check CPU usage"
        assert plan.hypothesis_id == "h-01"

        agent_action = plan.to_agent_action()
        assert agent_action.tool_name == "query_metrics"
        assert agent_action.params == {"service": "checkout-db"}
        assert agent_action.purpose == "Check CPU usage"
        assert agent_action.hypothesis_id == "h-01"

    def test_mock_planner_queue(self):
        plan1 = PlannedAction(tool_name="tool_1", purpose="Step 1")
        plan2 = PlannedAction(tool_name="tool_2", purpose="Step 2")
        planner = MockPlanner([plan1, plan2])

        scenario = create_checkout_scenario()
        state = InvestigationState(incident=scenario.incident)

        assert planner.plan_next_action(state, []) == plan1
        assert planner.plan_next_action(state, []) == plan2
        assert planner.plan_next_action(state, []) is None


class TestToolRegistry:
    def test_register_and_lookup(self):
        scenario = create_checkout_scenario()
        tool = QueryMetricsTool(scenario)
        registry = ToolRegistry()
        registry.register(tool)

        assert registry.has("query_metrics") is True
        assert registry.get("query_metrics") is tool
        assert registry.get_or_raise("query_metrics") is tool

    def test_unknown_tool_fails_safely(self):
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None
        with pytest.raises(ToolNotFoundError):
            registry.get_or_raise("nonexistent")

    def test_duplicate_registration_rejected(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry()
        registry.register(QueryMetricsTool(scenario))
        with pytest.raises(DuplicateToolError):
            registry.register(QueryMetricsTool(scenario))

    def test_reject_invalid_tool_type(self):
        registry = ToolRegistry()
        with pytest.raises(TypeError):
            registry.register("not_a_tool")  # type: ignore

    def test_metadata_exposure(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([
            QueryMetricsTool(scenario),
            GrepLogsTool(scenario),
        ])
        meta = registry.get_metadata()
        assert len(meta) == 2
        names = {m["name"] for m in meta}
        assert names == {"query_metrics", "grep_logs"}
        assert "parameters_schema" in meta[0]


class TestActionValidation:
    def setup_method(self):
        self.scenario = create_checkout_scenario()
        self.registry = ToolRegistry([
            QueryMetricsTool(self.scenario),
            GrepLogsTool(self.scenario),
        ])
        self.planner = MockPlanner()
        self.controller = AgentController(
            planner=self.planner,
            tool_registry=self.registry,
        )
        self.state = InvestigationState(incident=self.scenario.incident)

    def test_missing_purpose_rejected(self):
        action = PlannedAction(
            tool_name="query_metrics",
            parameters={"service": "checkout-db"},
            purpose="",  # empty
        )
        with pytest.raises(ActionValidationError, match="non-empty purpose"):
            self.controller.validate_action(action, self.state)

    def test_unknown_tool_rejected(self):
        action = PlannedAction(
            tool_name="arbitrary_bash_cmd",
            parameters={},
            purpose="Run command",
        )
        with pytest.raises(ActionValidationError, match="Unknown tool"):
            self.controller.validate_action(action, self.state)

    def test_missing_required_parameter_rejected(self):
        # QueryMetrics requires "service" parameter
        action = PlannedAction(
            tool_name="query_metrics",
            parameters={},  # missing "service"
            purpose="Query metrics without service",
        )
        with pytest.raises(ActionValidationError, match="Missing required parameter"):
            self.controller.validate_action(action, self.state)

    def test_unknown_hypothesis_id_rejected(self):
        action = PlannedAction(
            tool_name="query_metrics",
            parameters={"service": "checkout-db"},
            purpose="Valid purpose",
            hypothesis_id="hyp-does-not-exist",
        )
        with pytest.raises(ActionValidationError, match="Target hypothesis ID .* does not exist"):
            self.controller.validate_action(action, self.state)


class TestPrimaryScenarioSingleControlledStep:
    """Requirement 12: Single controlled step on primary scenario."""

    def test_controlled_step_disproves_h1(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])

        h1 = Hypothesis(id="h1-db", statement="Database overload")
        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=[h1],
            current_hypothesis_id="h1-db",
            investigation_status=InvestigationStatus.ACTIVE,
        )

        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Check database resource usage to evaluate H1",
                hypothesis_id="h1-db",
            )
        ])

        controller = AgentController(planner=planner, tool_registry=registry)
        step_result = controller.step(state)

        assert step_result is not None
        assert isinstance(step_result, ControllerStepResult)

        # 1. Action was recorded
        assert len(state.actions_taken) == 1
        assert state.actions_taken[0].tool_name == "query_metrics"

        # 2. Evidence was created with provenance
        assert len(state.evidence) == 1
        ev = state.evidence[0]
        assert ev.source_tool == "query_metrics"
        assert ev.tool_succeeded is True
        assert ev.raw_data["metrics"]["cpu_percent"] == 12.3

        # 3. Evaluator evaluated evidence against H1
        assert step_result.evaluation is not None
        assert step_result.evaluation.relationship == EvaluationRelationship.CONTRADICTS
        assert step_result.evaluation.score_delta == -3

        # 4. H1 became DISPROVEN
        h1_updated = controller.hypothesis_manager.get("h1-db")
        assert h1_updated is not None
        assert h1_updated.status == HypothesisStatus.DISPROVEN
        assert ev.id in h1_updated.contradicting_evidence_ids

        # 5. Decision trace recorded step
        assert len(state.decision_trace) == 1
        trace = state.decision_trace[0]
        assert trace.step_number == 1
        assert trace.action == "query_metrics"
        assert "CONTRADICTS (-3)" in trace.evaluation
        assert trace.hypothesis_status == "disproven"


class TestToolFailureAndSafetyLimits:
    def test_failed_tool_call_handled_safely(self):
        scenario = create_checkout_scenario(
            tool_failures={
                "fetch_recent_commits": ToolFailureConfig(
                    enabled=True,
                    error_message="HTTP 503: Service Unavailable",
                )
            }
        )
        registry = ToolRegistry([FetchRecentCommitsTool(scenario)])
        h2 = Hypothesis(id="h2-leak", statement="Checkout connection leak")
        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=[h2],
            current_hypothesis_id="h2-leak",
            investigation_status=InvestigationStatus.ACTIVE,
        )

        planner = MockPlanner([
            PlannedAction(
                tool_name="fetch_recent_commits",
                parameters={"service": "checkout"},
                purpose="Inspect recent commits for connection changes",
                hypothesis_id="h2-leak",
            )
        ])

        controller = AgentController(planner=planner, tool_registry=registry)
        step_result = controller.step(state)

        assert step_result is not None
        assert step_result.result.success is False
        assert "503" in step_result.result.error

        # Evidence preserved as failure provenance
        assert len(state.evidence) == 1
        ev = state.evidence[0]
        assert ev.tool_succeeded is False

        # Evaluation is NEUTRAL, not root-cause
        assert step_result.evaluation is not None
        assert step_result.evaluation.relationship == EvaluationRelationship.NEUTRAL
        assert step_result.evaluation.score_delta == 0

        # Failed evidence was NOT attached as supporting root cause
        h2_updated = controller.hypothesis_manager.get("h2-leak")
        assert ev.id not in h2_updated.supporting_evidence_ids

    def test_max_steps_limit_enforced(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([GrepLogsTool(scenario)])
        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=InvestigationStatus.ACTIVE,
        )

        # Queue 5 actions but set max_steps=2
        actions = [
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": f"p{i}"},
                purpose=f"Step {i}",
            )
            for i in range(5)
        ]
        planner = MockPlanner(actions)
        controller = AgentController(planner=planner, tool_registry=registry, max_steps=2)

        controller.run(state)
        assert len(state.actions_taken) == 2
        assert state.investigation_status == InvestigationStatus.EXHAUSTED

    def test_no_execution_after_resolved(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=InvestigationStatus.RESOLVED,
        )
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Test",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)

        result = controller.step(state)
        assert result is None
        assert len(state.actions_taken) == 0


class TestDeterminism:
    def test_mock_planner_reproducibility(self):
        def _run_once():
            scenario = create_checkout_scenario()
            registry = ToolRegistry([QueryMetricsTool(scenario)])
            h1 = Hypothesis(id="h1", statement="Database overload")
            state = InvestigationState(
                incident=scenario.incident,
                hypotheses=[h1],
                current_hypothesis_id="h1",
                investigation_status=InvestigationStatus.ACTIVE,
            )
            planner = MockPlanner([
                PlannedAction(
                    tool_name="query_metrics",
                    parameters={"service": "checkout-db"},
                    purpose="Test CPU",
                    hypothesis_id="h1",
                )
            ])
            controller = AgentController(planner=planner, tool_registry=registry)
            controller.step(state)
            return state

        state_1 = _run_once()
        state_2 = _run_once()

        assert len(state_1.actions_taken) == len(state_2.actions_taken) == 1
        assert len(state_1.evidence) == len(state_2.evidence) == 1
        assert state_1.evidence[0].raw_data == state_2.evidence[0].raw_data
        assert state_1.decision_trace[0].evaluation == state_2.decision_trace[0].evaluation
