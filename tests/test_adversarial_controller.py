"""Adversarial and robustness tests for AgentController and ToolRegistry.

Covers:
- Category E: Invalid Tool
- Category F: Invalid Parameters
- Category G: Invalid Hypothesis ID
- Category H: None / Empty Planner Result
- Category L: Terminal State Guard (Controller level)
- Category Q: Tool Registry Integrity
- Invariant 1: Action validation rejection prevents tool execution
"""

from __future__ import annotations

from trace.agent.controller import (
    ActionValidationError,
    AgentController,
)
from trace.agent.planner import MockPlanner, PlannedAction
from trace.agent.registry import (
    DuplicateToolError,
    ToolNotFoundError,
    ToolRegistry,
)
from trace.engine.state import InvestigationState, InvestigationStatus
from trace.models.hypothesis import Hypothesis
from trace.simulator.scenarios.checkout_502 import create as create_checkout_scenario
from trace.tools.fetch_commits import FetchRecentCommitsTool
from trace.tools.grep_logs import GrepLogsTool
from trace.tools.query_metrics import QueryMetricsTool

import pytest


class TestCategoryEInvalidTool:
    """Category E: Planner returns a PlannedAction referencing an unregistered tool."""

    def test_unregistered_tool_rejected_by_controller(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        planner = MockPlanner([
            PlannedAction(
                tool_name="rm_rf_database",
                parameters={"force": True},
                purpose="Attempt dangerous unregistered tool",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=InvestigationStatus.ACTIVE,
        )

        with pytest.raises(ActionValidationError) as exc_info:
            controller.step(state)

        assert "Unknown tool 'rm_rf_database'" in str(exc_info.value)
        assert len(state.actions_taken) == 0
        assert len(state.evidence) == 0
        assert len(state.decision_trace) == 0


class TestCategoryFInvalidParameters:
    """Category F: Planner returns a valid tool with missing or invalid parameters."""

    def test_missing_required_service_parameter(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        # Missing "service" required field for query_metrics
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={},
                purpose="Query metrics without service",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=InvestigationStatus.ACTIVE,
        )

        with pytest.raises(ActionValidationError) as exc_info:
            controller.step(state)

        assert "Missing required parameter" in str(exc_info.value)
        assert "service" in str(exc_info.value)
        assert len(state.actions_taken) == 0
        assert len(state.evidence) == 0

    def test_missing_required_pattern_parameter_for_grep_logs(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([GrepLogsTool(scenario)])
        # Missing "pattern" required field for grep_logs
        planner = MockPlanner([
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout"},
                purpose="Grep logs without pattern",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=InvestigationStatus.ACTIVE,
        )

        with pytest.raises(ActionValidationError) as exc_info:
            controller.step(state)

        assert "pattern" in str(exc_info.value)
        assert len(state.actions_taken) == 0
        assert len(state.evidence) == 0

    def test_empty_purpose_rejected(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="   ",  # Blank/whitespace purpose
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        state = InvestigationState(incident=scenario.incident)

        with pytest.raises(ActionValidationError) as exc_info:
            controller.step(state)

        assert "non-empty purpose" in str(exc_info.value)
        assert len(state.actions_taken) == 0


class TestCategoryGInvalidHypothesisId:
    """Category G: Planner references a nonexistent hypothesis ID."""

    def test_nonexistent_hypothesis_id_rejected(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        h_real = Hypothesis(id="h-real", statement="Real hypothesis")
        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=[h_real],
            investigation_status=InvestigationStatus.ACTIVE,
        )
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Test nonexistent hypothesis",
                hypothesis_id="h-ghost-999",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)

        with pytest.raises(ActionValidationError) as exc_info:
            controller.step(state)

        assert "Target hypothesis ID 'h-ghost-999' does not exist" in str(exc_info.value)
        assert len(state.actions_taken) == 0
        assert len(state.evidence) == 0
        assert h_real.status == Hypothesis.model_fields["status"].default


class TestCategoryHNoneEmptyPlannerResult:
    """Category H: Planner returns None."""

    def test_planner_returns_none_stops_step_cleanly(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        planner = MockPlanner([])  # Empty action queue -> returns None
        controller = AgentController(planner=planner, tool_registry=registry)
        state = InvestigationState(incident=scenario.incident)

        result = controller.step(state)
        assert result is None
        assert len(state.actions_taken) == 0
        assert len(state.evidence) == 0


class TestCategoryLTerminalStateGuardController:
    """Category L: Calling controller.step() on a terminal state returns None."""

    @pytest.mark.parametrize(
        "terminal_status",
        [
            InvestigationStatus.RESOLVED,
            InvestigationStatus.EXHAUSTED,
            InvestigationStatus.BLOCKED,
        ],
    )
    def test_controller_step_on_terminal_state_executes_zero_tools(self, terminal_status):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Attempt action on terminal state",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=terminal_status,
        )

        result = controller.step(state)
        assert result is None
        assert len(state.actions_taken) == 0
        assert len(state.evidence) == 0
        assert state.investigation_status == terminal_status


class TestCategoryQToolRegistryIntegrity:
    """Category Q: ToolRegistry configuration and boundary validation."""

    def test_duplicate_tool_registration_rejected(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])

        with pytest.raises(DuplicateToolError, match="already registered"):
            registry.register(QueryMetricsTool(scenario))

    def test_non_base_tool_registration_rejected(self):
        registry = ToolRegistry()
        with pytest.raises(TypeError, match="Expected BaseTool instance"):
            registry.register("not_a_tool")  # type: ignore[arg-type]

    def test_get_or_raise_unregistered_tool(self):
        registry = ToolRegistry()
        with pytest.raises(ToolNotFoundError, match="not registered"):
            registry.get_or_raise("unregistered_tool")

    def test_get_unregistered_tool_returns_none(self):
        registry = ToolRegistry()
        assert registry.get("unregistered_tool") is None
        assert registry.has("unregistered_tool") is False

    def test_registry_metadata_contains_parameters_schema(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([
            QueryMetricsTool(scenario),
            GrepLogsTool(scenario),
            FetchRecentCommitsTool(scenario),
        ])
        metadata = registry.get_metadata()
        assert len(metadata) == 3

        names = {m["name"] for m in metadata}
        assert names == {"query_metrics", "grep_logs", "fetch_recent_commits"}

        for tool_meta in metadata:
            assert "description" in tool_meta
            assert "parameters_schema" in tool_meta
            assert "properties" in tool_meta["parameters_schema"]
            assert "required" in tool_meta["parameters_schema"]


class TestInvariant1ValidationPreventsExecution:
    """Invariant 1: A tool cannot execute if Controller validation rejects its action."""

    def test_invariant_validation_failure_leaves_state_completely_unmodified(self):
        scenario = create_checkout_scenario()
        tool = QueryMetricsTool(scenario)
        registry = ToolRegistry([tool])

        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=InvestigationStatus.ACTIVE,
        )

        planner = MockPlanner([
            # Bad action 1: wrong tool
            PlannedAction(tool_name="bad_tool", parameters={}, purpose="P"),
            # Bad action 2: missing parameter
            PlannedAction(tool_name="query_metrics", parameters={}, purpose="P"),
            # Bad action 3: bad hypothesis
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="P",
                hypothesis_id="nonexistent",
            ),
        ])
        controller = AgentController(planner=planner, tool_registry=registry)

        for _ in range(3):
            with pytest.raises(ActionValidationError):
                controller.step(state)

        # Invariant check: zero executions, zero evidence, zero trace steps
        assert len(state.actions_taken) == 0
        assert len(state.evidence) == 0
        assert len(state.decision_trace) == 0
        assert state.investigation_status == InvestigationStatus.ACTIVE
