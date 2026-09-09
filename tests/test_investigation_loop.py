"""Comprehensive deterministic tests for AutonomousInvestigationLoop."""

from __future__ import annotations

from trace.agent.controller import AgentController
from trace.agent.investigation_loop import AutonomousInvestigationLoop
from trace.agent.llm_planner import build_planner_context
from trace.agent.planner import BasePlanner, MockPlanner, PlannedAction
from trace.agent.registry import ToolRegistry
from trace.engine.state import InvestigationState, InvestigationStatus
from trace.models.hypothesis import Hypothesis, HypothesisStatus
from trace.simulator.models import ToolFailureConfig
from trace.simulator.scenarios.checkout_502 import create as create_checkout_scenario
from trace.tools.fetch_commits import FetchRecentCommitsTool
from trace.tools.grep_logs import GrepLogsTool
from trace.tools.query_metrics import QueryMetricsTool
from typing import Any


class AdaptiveTestPlanner(BasePlanner):
    """An adaptive mock planner for testing that inspects state dynamically.

    Demonstrates true adaptive behavior:
    1. If H1 is not disproven, plans query_metrics for H1.
    2. Once H1 is disproven, switches focus to H2:
       - Plans grep_logs if logs haven't been collected.
       - Plans fetch_recent_commits if commits haven't been collected.
    3. If H2 is supported or no active hypothesis remains, returns None.
    """

    def __init__(self) -> None:
        self.states_observed: list[InvestigationState] = []

    def plan_next_action(
        self,
        state: InvestigationState,
        available_tools: list[dict[str, Any]],
    ) -> PlannedAction | None:
        # Record state observed for test assertions
        self.states_observed.append(state)

        h1 = state.get_hypothesis("h1-db")
        h2 = state.get_hypothesis("h2-leak")

        # 1. If H1 is active (not terminal), investigate H1 with metrics
        if h1 and not h1.is_terminal():
            return PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Inspect checkout-db metrics to evaluate H1",
                hypothesis_id="h1-db",
            )

        # 2. If H1 is disproven and H2 is active, adaptively investigate H2
        if h2 and not h2.is_terminal():
            collected_tools = {ev.source_tool for ev in state.evidence}
            if "grep_logs" not in collected_tools:
                return PlannedAction(
                    tool_name="grep_logs",
                    parameters={"service": "checkout", "pattern": "connection"},
                    purpose="Search for connection pool errors to test H2",
                    hypothesis_id="h2-leak",
                )
            if "fetch_recent_commits" not in collected_tools:
                return PlannedAction(
                    tool_name="fetch_recent_commits",
                    parameters={"service": "checkout", "limit": 5},
                    purpose="Inspect recent commits for connection leaks to test H2",
                    hypothesis_id="h2-leak",
                )

        # 3. No further active hypotheses
        return None


class TestAdaptiveInvestigationLoop:
    """Requirement A & Red-Herring: Full adaptive loop with mock planner."""

    def test_successful_adaptive_red_herring_and_resolution(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([
            QueryMetricsTool(scenario),
            GrepLogsTool(scenario),
            FetchRecentCommitsTool(scenario),
        ])

        h1 = Hypothesis(id="h1-db", statement="Database overload")
        h2 = Hypothesis(id="h2-leak", statement="Checkout connection leak")
        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=[h1, h2],
            investigation_status=InvestigationStatus.ACTIVE,
        )

        planner = AdaptiveTestPlanner()
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=10)

        final_state = loop.run(state)

        # 1. State reached RESOLVED
        assert final_state.investigation_status == InvestigationStatus.RESOLVED

        # 2. H1 was disproven by contradictory metrics
        h1_final = final_state.get_hypothesis("h1-db")
        assert h1_final is not None
        assert h1_final.status == HypothesisStatus.DISPROVEN
        assert len(h1_final.contradicting_evidence_ids) == 1

        # 3. H2 was supported by logs + commit confirmation
        h2_final = final_state.get_hypothesis("h2-leak")
        assert h2_final is not None
        assert h2_final.status == HypothesisStatus.SUPPORTED
        assert len(h2_final.supporting_evidence_ids) == 2

        # 4. Exactly 3 adaptive steps were taken (metrics -> logs -> commits)
        assert len(final_state.actions_taken) == 3
        assert [a.tool_name for a in final_state.actions_taken] == [
            "query_metrics",
            "grep_logs",
            "fetch_recent_commits",
        ]

        # 5. Planner was called 3 times, observing dynamically updated states
        assert len(planner.states_observed) == 3

        # On call 2, planner observed that H1 was already DISPROVEN
        state_at_call_2 = planner.states_observed[1]
        observed_h1 = state_at_call_2.get_hypothesis("h1-db")
        assert observed_h1 is not None
        assert observed_h1.status == HypothesisStatus.DISPROVEN

        # On call 3, planner observed grep_logs evidence
        state_at_call_3 = planner.states_observed[2]
        assert any(e.source_tool == "grep_logs" for e in state_at_call_3.evidence)

        # 6. Complete decision trace accumulated without chain-of-thought
        assert len(final_state.decision_trace) == 3
        assert final_state.decision_trace[0].action == "query_metrics"
        assert "CONTRADICTS (-3)" in final_state.decision_trace[0].evaluation
        assert final_state.decision_trace[0].hypothesis_status == "disproven"

        assert final_state.decision_trace[1].action == "grep_logs"
        assert "SUPPORTS (2)" in final_state.decision_trace[1].evaluation

        assert final_state.decision_trace[2].action == "fetch_recent_commits"
        assert "SUPPORTS (3)" in final_state.decision_trace[2].evaluation
        assert final_state.decision_trace[2].hypothesis_status == "supported"


class TestStoppingConditions:
    def test_planner_returns_none_stops_cleanly(self):
        """Requirement B: Planner returns None terminates cleanly."""
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        state = InvestigationState(incident=scenario.incident)

        planner = MockPlanner([])  # Empty queue -> returns None
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=5)

        final_state = loop.run(state)
        assert final_state.investigation_status == InvestigationStatus.EXHAUSTED
        assert len(final_state.actions_taken) == 0

    def test_step_budget_exhausted(self):
        """Requirement C: Step budget exhausted terminates loop."""
        scenario = create_checkout_scenario()
        registry = ToolRegistry([GrepLogsTool(scenario)])
        state = InvestigationState(incident=scenario.incident)

        # Queue 6 actions, budget is 2
        actions = [
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": f"pat-{i}"},
                purpose=f"Test {i}",
            )
            for i in range(6)
        ]
        planner = MockPlanner(actions)
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=2)

        final_state = loop.run(state)
        assert len(final_state.actions_taken) == 2
        assert final_state.investigation_status == InvestigationStatus.EXHAUSTED

    def test_already_resolved_state_takes_zero_actions(self):
        """Requirement E: Already resolved state executes zero actions."""
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
                purpose="T",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller)

        final_state = loop.run(state)
        assert len(final_state.actions_taken) == 0
        assert final_state.investigation_status == InvestigationStatus.RESOLVED

    def test_already_exhausted_or_blocked_state_takes_zero_actions(self):
        """Requirement F: Already exhausted or blocked state executes zero actions."""
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])

        for status in (InvestigationStatus.EXHAUSTED, InvestigationStatus.BLOCKED):
            state = InvestigationState(
                incident=scenario.incident,
                investigation_status=status,
            )
            planner = MockPlanner([
                PlannedAction(
                    tool_name="query_metrics",
                    parameters={"service": "checkout-db"},
                    purpose="T",
                )
            ])
            controller = AgentController(planner=planner, tool_registry=registry)
            loop = AutonomousInvestigationLoop(controller=controller)

            final_state = loop.run(state)
            assert len(final_state.actions_taken) == 0
            assert final_state.investigation_status == status


class TestFailureSafetyAndRobustness:
    def test_tool_failure_recorded_and_replan_allowed(self):
        """Requirement D: Tool failure does not crash loop and allows re-planning."""
        scenario = create_checkout_scenario(
            tool_failures={
                "fetch_recent_commits": ToolFailureConfig(
                    enabled=True,
                    error_message="HTTP 503: Service Unavailable",
                )
            }
        )
        registry = ToolRegistry([
            FetchRecentCommitsTool(scenario),
            GrepLogsTool(scenario),
        ])

        h = Hypothesis(id="h-leak", statement="Checkout connection leak")
        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=[h],
            investigation_status=InvestigationStatus.ACTIVE,
        )

        # Step 1: commits tool fails with 503
        # Step 2: planner adapts and tries grep_logs
        planner = MockPlanner([
            PlannedAction(
                tool_name="fetch_recent_commits",
                parameters={"service": "checkout"},
                purpose="Try commit check",
                hypothesis_id="h-leak",
            ),
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": "connection"},
                purpose="Fallback to logs after commit failure",
                hypothesis_id="h-leak",
            ),
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=5)

        final_state = loop.run(state)

        assert len(final_state.actions_taken) == 2
        # Action 1 failed
        ev1 = final_state.evidence[0]
        assert ev1.tool_succeeded is False
        assert "503" in ev1.error_message
        # Failed tool was not attached as supporting root cause
        assert ev1.id not in h.supporting_evidence_ids

        # Action 2 succeeded
        ev2 = final_state.evidence[1]
        assert ev2.tool_succeeded is True
        assert ev2.id in h.supporting_evidence_ids

    def test_unregistered_tool_fails_safely_without_crash(self):
        """Loop catches ActionValidationError and halts safely with BLOCKED status."""
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        state = InvestigationState(incident=scenario.incident)

        planner = MockPlanner([
            PlannedAction(
                tool_name="unregistered_malicious_tool",
                parameters={},
                purpose="Attempt invalid tool",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller)

        final_state = loop.run(state)
        assert final_state.investigation_status == InvestigationStatus.BLOCKED
        assert len(final_state.actions_taken) == 0
        assert len(final_state.decision_trace) == 1
        assert "rejected" in final_state.decision_trace[0].observation.lower()

    def test_consecutive_duplicate_action_protection(self):
        """Loop detects runaway duplicate actions and halts with BLOCKED status."""
        scenario = create_checkout_scenario()
        registry = ToolRegistry([GrepLogsTool(scenario)])
        state = InvestigationState(incident=scenario.incident)

        # Propose the exact same action 4 times in a row
        actions = [
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": "error"},
                purpose="Check logs",
            )
            for _ in range(4)
        ]
        planner = MockPlanner(actions)
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(
            controller=controller,
            max_steps=10,
            max_consecutive_duplicates=3,
        )

        final_state = loop.run(state)
        # Halts at 3 consecutive duplicates rather than spinning through all 10
        assert len(final_state.actions_taken) == 3
        assert final_state.investigation_status == InvestigationStatus.BLOCKED


class TestGroundTruthIsolation:
    def test_ground_truth_never_passed_to_planner_context(self):
        """Regression test: planner context never contains ground_truth_root_cause."""
        scenario = create_checkout_scenario()
        assert scenario.incident.ground_truth_root_cause is not None
        secret = scenario.incident.ground_truth_root_cause

        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=[Hypothesis(id="h1", statement="Test")],
        )

        context = build_planner_context(state, [])
        context_str = context.model_dump_json()

        assert "ground_truth_root_cause" not in context_str
        assert secret not in context_str
