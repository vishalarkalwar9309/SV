"""Adversarial and robustness tests for AutonomousInvestigationLoop.

Covers:
- Category A: Tool Failure (503, retry/re-plan safety, bounded failure loop)
- Category C: Contradictory Evidence (negative weight, disproval, safe switching)
- Category D: Red Herring Investigation (checkout_502 adaptive disproval and resolution)
- Category I: Planner Exception (safety, BLOCKED transition, no tool execution, secret safety)
- Category J: Step Budget (exact max_steps enforcement: 0, 1, small positive)
- Category K: Duplicate Action Loop (consecutive duplicate detection and non-consecutive reset)
- Category L: Terminal State Guard (loop level: RESOLVED, EXHAUSTED, BLOCKED)
- Invariants 4, 5, 6
"""

from __future__ import annotations

from trace.agent.controller import AgentController
from trace.agent.investigation_loop import AutonomousInvestigationLoop
from trace.agent.llm_planner import PlannerError
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

import pytest


class TestCategoryAToolFailure:
    """Category A: Registered tool returns simulated failure (e.g. HTTP 503)."""

    def test_tool_failure_recorded_safely_and_replan_succeeds(self):
        scenario = create_checkout_scenario(
            tool_failures={
                "query_metrics": ToolFailureConfig(
                    enabled=True,
                    error_message="HTTP 503 Service Unavailable: metrics backend down",
                )
            }
        )
        registry = ToolRegistry([
            QueryMetricsTool(scenario),
            GrepLogsTool(scenario),
            FetchRecentCommitsTool(scenario),
        ])

        h1 = Hypothesis(id="h1-db", statement="Database overload")
        h2 = Hypothesis(id="h2-leak", statement="Connection pool exhaustion")
        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=[h1, h2],
            investigation_status=InvestigationStatus.ACTIVE,
        )

        # Step 1: query_metrics fails with 503
        # Step 2: planner recovers and queries logs for h2
        # Step 3: planner queries commits for h2 -> reaches resolution
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Attempt metrics collection",
                hypothesis_id="h1-db",
            ),
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": "connection"},
                purpose="Recover by checking application logs",
                hypothesis_id="h2-leak",
            ),
            PlannedAction(
                tool_name="fetch_recent_commits",
                parameters={"service": "checkout", "limit": 5},
                purpose="Confirm with recent commits",
                hypothesis_id="h2-leak",
            ),
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=5)

        final_state = loop.run(state)

        # 1. Failed ActionResult was recorded
        assert len(final_state.actions_taken) == 3
        # 2. Failed tool call produces failed evidence
        assert len(final_state.evidence) == 3
        failed_ev = final_state.evidence[0]
        assert failed_ev.tool_succeeded is False
        assert "503 Service Unavailable" in failed_ev.error_message
        assert failed_ev.source_tool == "query_metrics"

        # 3. Failure is NOT treated as supporting or contradicting evidence
        assert failed_ev.id not in h1.supporting_evidence_ids
        assert failed_ev.id not in h1.contradicting_evidence_ids
        assert failed_ev.id not in h2.supporting_evidence_ids
        assert failed_ev.id not in h2.contradicting_evidence_ids

        # 4. Evaluator contributes neutral evidence weight in decision trace
        assert final_state.decision_trace[0].action == "query_metrics"
        assert "NEUTRAL (0)" in final_state.decision_trace[0].evaluation

        # 5. Investigation did not crash and planner successfully re-planned
        assert final_state.investigation_status == InvestigationStatus.RESOLVED
        assert h2.status == HypothesisStatus.SUPPORTED

        # 6. No fabricated successful tool result appears
        assert final_state.evidence[0].raw_data == {}

    def test_repeated_tool_failures_lead_to_bounded_outcome(self):
        """Repeated tool failures exhaust budget without crashing or looping infinitely."""
        scenario = create_checkout_scenario(
            tool_failures={
                "grep_logs": ToolFailureConfig(
                    enabled=True,
                    error_message="HTTP 500 Internal Server Error",
                )
            }
        )
        registry = ToolRegistry([GrepLogsTool(scenario)])
        state = InvestigationState(incident=scenario.incident)

        # Continually proposes varying grep actions that all fail
        actions = [
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": f"error-{i}"},
                purpose=f"Search pattern {i}",
            )
            for i in range(10)
        ]
        planner = MockPlanner(actions)
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=3)

        final_state = loop.run(state)

        # Terminated at exact budget of 3 steps
        assert len(final_state.actions_taken) == 3
        assert len(final_state.evidence) == 3
        assert all(not ev.tool_succeeded for ev in final_state.evidence)
        assert final_state.investigation_status == InvestigationStatus.EXHAUSTED


class TestCategoryCContradictoryEvidence:
    """Category C: Contradictory evidence evaluation and hypothesis disproval."""

    def test_contradictory_evidence_disproves_hypothesis_deterministically(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([
            QueryMetricsTool(scenario),
            GrepLogsTool(scenario),
            FetchRecentCommitsTool(scenario),
        ])

        h_db = Hypothesis(id="h-db", statement="Database overload")
        h_leak = Hypothesis(id="h-leak", statement="Checkout connection leak")
        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=[h_db, h_leak],
            investigation_status=InvestigationStatus.ACTIVE,
        )

        # In checkout_502, checkout-db CPU is 8.5% (normal).
        # Querying checkout-db metrics directly contradicts the DB overload hypothesis.
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Test if DB is overloaded",
                hypothesis_id="h-db",
            ),
            # Move to second hypothesis
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": "connection"},
                purpose="Test connection pool hypothesis",
                hypothesis_id="h-leak",
            ),
            PlannedAction(
                tool_name="fetch_recent_commits",
                parameters={"service": "checkout", "limit": 5},
                purpose="Verify commit causing leak",
                hypothesis_id="h-leak",
            ),
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=5)

        final_state = loop.run(state)

        # 1. Contradiction receives correct deterministic negative weight (-3)
        assert "CONTRADICTS (-3)" in final_state.decision_trace[0].evaluation

        # 2. H_db becomes DISPROVEN
        assert h_db.status == HypothesisStatus.DISPROVEN
        assert len(h_db.contradicting_evidence_ids) == 1
        assert len(h_db.supporting_evidence_ids) == 0
        assert h_db.disproval_reason is not None

        # 3. Decision trace accurately reflects the evaluation
        assert final_state.decision_trace[0].hypothesis_status == "disproven"

        # 4. Agent successfully moved toward h_leak
        assert h_leak.status == HypothesisStatus.SUPPORTED
        assert len(h_leak.supporting_evidence_ids) == 2

        # 5. Old hypothesis does not remain SUPPORTED
        assert h_db.status != HypothesisStatus.SUPPORTED
        assert final_state.investigation_status == InvestigationStatus.RESOLVED


class TestCategoryDRedHerringInvestigation:
    """Category D: Full red herring adaptive investigation on checkout_502."""

    def test_full_red_herring_adaptive_sequence(self):
        """Verify the exact sequence:

        1. initial hypothesis: DB overload (H1)
        2. query metrics
        3. observe DB CPU is low
        4. DB overload receives contradictory evidence (-3)
        5. H1 becomes DISPROVEN
        6. investigation considers another explanation: connection leak (H2)
        7. connection leak hypothesis receives supporting evidence (+2)
        8. recent commit supports it (+3)
        9. investigation reaches RESOLVED
        """
        scenario = create_checkout_scenario()
        registry = ToolRegistry([
            QueryMetricsTool(scenario),
            GrepLogsTool(scenario),
            FetchRecentCommitsTool(scenario),
        ])

        h1 = Hypothesis(id="h1-db", statement="Database overload")
        h2 = Hypothesis(id="h2-leak", statement="Connection pool leak")
        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=[h1, h2],
            investigation_status=InvestigationStatus.ACTIVE,
        )

        class AdaptiveRedHerringPlanner(BasePlanner):
            def plan_next_action(
                self, state: InvestigationState, available_tools: list[dict[str, Any]]
            ) -> PlannedAction | None:
                h1_current = state.get_hypothesis("h1-db")
                h2_current = state.get_hypothesis("h2-leak")

                # Step 1: If H1 is not terminal, query DB metrics
                if h1_current and not h1_current.is_terminal():
                    return PlannedAction(
                        tool_name="query_metrics",
                        parameters={"service": "checkout-db"},
                        purpose="Check DB CPU usage for H1",
                        hypothesis_id="h1-db",
                    )

                # Step 2 & 3: Once H1 is disproven, pursue H2
                if h2_current and not h2_current.is_terminal():
                    has_logs = any(ev.source_tool == "grep_logs" for ev in state.evidence)
                    if not has_logs:
                        return PlannedAction(
                            tool_name="grep_logs",
                            parameters={"service": "checkout", "pattern": "connection"},
                            purpose="Check connection errors in logs for H2",
                            hypothesis_id="h2-leak",
                        )
                    has_commits = any(
                        ev.source_tool == "fetch_recent_commits" for ev in state.evidence
                    )
                    if not has_commits:
                        return PlannedAction(
                            tool_name="fetch_recent_commits",
                            parameters={"service": "checkout", "limit": 5},
                            purpose="Check recent commits for connection leak for H2",
                            hypothesis_id="h2-leak",
                        )
                return None

        planner = AdaptiveRedHerringPlanner()
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=10)

        final_state = loop.run(state)

        # Verify step-by-step observable state
        assert len(final_state.actions_taken) == 3

        # 1-5: H1 disproven by metrics
        assert h1.status == HypothesisStatus.DISPROVEN
        assert len(h1.contradicting_evidence_ids) == 1
        assert "CONTRADICTS (-3)" in final_state.decision_trace[0].evaluation

        # 6-8: H2 supported by logs and commits
        assert h2.status == HypothesisStatus.SUPPORTED
        assert len(h2.supporting_evidence_ids) == 2
        assert "SUPPORTS (2)" in final_state.decision_trace[1].evaluation
        assert "SUPPORTS (3)" in final_state.decision_trace[2].evaluation

        # 9: Investigation reached RESOLVED
        assert final_state.investigation_status == InvestigationStatus.RESOLVED


class TestCategoryIPlannerException:
    """Category I: Planner raises an exception."""

    def test_planner_error_transitions_to_blocked_safely(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])

        class FaultyPlanner(BasePlanner):
            def plan_next_action(
                self, state: InvestigationState, available_tools: list[dict[str, Any]]
            ) -> PlannedAction | None:
                raise PlannerError("Simulated LLM API 500: rate limit exceeded")

        planner = FaultyPlanner()
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=5)

        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=[Hypothesis(id="h1", statement="Test")],
            investigation_status=InvestigationStatus.ACTIVE,
        )

        final_state = loop.run(state)

        # 1. Investigation does not crash and transitions to BLOCKED
        assert final_state.investigation_status == InvestigationStatus.BLOCKED

        # 2. No tool executes
        assert len(final_state.actions_taken) == 0
        assert len(final_state.evidence) == 0

        # 3. Decision trace records the planner error step
        assert len(final_state.decision_trace) == 1
        assert final_state.decision_trace[0].action == "planner_failure"
        assert "rate limit exceeded" in final_state.decision_trace[0].observation

        # 4. Previous state remains intact
        assert len(final_state.hypotheses) == 1
        assert final_state.hypotheses[0].status == HypothesisStatus.PROPOSED

    def test_planner_exception_after_partial_progress_preserves_evidence(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])

        call_count = 0

        class FailOnSecondStepPlanner(BasePlanner):
            def plan_next_action(
                self, state: InvestigationState, available_tools: list[dict[str, Any]]
            ) -> PlannedAction | None:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return PlannedAction(
                        tool_name="query_metrics",
                        parameters={"service": "checkout-db"},
                        purpose="Valid first step",
                    )
                raise PlannerError("Fatal planner crash on step 2")

        planner = FailOnSecondStepPlanner()
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=5)

        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=InvestigationStatus.ACTIVE,
        )

        final_state = loop.run(state)

        # Successfully executed step 1, blocked on step 2
        assert final_state.investigation_status == InvestigationStatus.BLOCKED
        assert len(final_state.actions_taken) == 1
        assert len(final_state.evidence) == 1
        assert len(final_state.decision_trace) == 2
        assert final_state.decision_trace[0].action == "query_metrics"
        assert final_state.decision_trace[1].action == "planner_failure"


class TestCategoryJStepBudget:
    """Category J: Investigation strictly respects step budgets."""

    def test_budget_zero_executes_zero_actions(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Action on zero budget",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=0)

        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=InvestigationStatus.ACTIVE,
        )

        final_state = loop.run(state)
        assert len(final_state.actions_taken) == 0
        assert len(final_state.evidence) == 0
        assert final_state.investigation_status == InvestigationStatus.EXHAUSTED

    def test_budget_one_executes_exactly_one_action(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario), GrepLogsTool(scenario)])
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Action 1",
            ),
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": "error"},
                purpose="Action 2",
            ),
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=1)

        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=InvestigationStatus.ACTIVE,
        )

        final_state = loop.run(state)
        assert len(final_state.actions_taken) == 1
        assert len(final_state.evidence) == 1
        assert final_state.investigation_status == InvestigationStatus.EXHAUSTED

    def test_small_positive_budget_exhaustion_terminates_loop(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([GrepLogsTool(scenario)])
        actions = [
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": f"query-{i}"},
                purpose=f"Step {i}",
            )
            for i in range(10)
        ]
        planner = MockPlanner(actions)
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=3)

        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=InvestigationStatus.ACTIVE,
        )

        final_state = loop.run(state)
        assert len(final_state.actions_taken) == 3
        assert len(final_state.evidence) == 3
        assert final_state.investigation_status == InvestigationStatus.EXHAUSTED


class TestCategoryKDuplicateActionLoop:
    """Category K: Consecutive duplicate action protection."""

    def test_consecutive_duplicate_protection_halts_loop(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([GrepLogsTool(scenario)])
        actions = [
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": "repeat"},
                purpose="Identical action",
            )
            for _ in range(5)
        ]
        planner = MockPlanner(actions)
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(
            controller=controller,
            max_steps=10,
            max_consecutive_duplicates=3,
        )

        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=InvestigationStatus.ACTIVE,
        )

        final_state = loop.run(state)
        # Halts after 3 consecutive identical actions
        assert len(final_state.actions_taken) == 3
        assert final_state.investigation_status == InvestigationStatus.BLOCKED

    def test_distinct_actions_reset_consecutive_duplicate_count(self):
        """Alternating actions do not trigger consecutive duplicate protection."""
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario), GrepLogsTool(scenario)])

        # Alternating sequence: A, B, A, B (4 steps, limit is 3)
        actions = [
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Action A",
            ),
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": "err"},
                purpose="Action B",
            ),
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Action A again",
            ),
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": "err"},
                purpose="Action B again",
            ),
        ]
        planner = MockPlanner(actions)
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(
            controller=controller,
            max_steps=4,
            max_consecutive_duplicates=3,
        )

        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=InvestigationStatus.ACTIVE,
        )

        final_state = loop.run(state)
        # All 4 actions executed without being blocked because duplicates were non-consecutive
        assert len(final_state.actions_taken) == 4
        assert final_state.investigation_status == InvestigationStatus.EXHAUSTED


class TestCategoryLTerminalStateGuardLoop:
    """Category L: Terminal state protection at loop level."""

    @pytest.mark.parametrize(
        "terminal_status",
        [
            InvestigationStatus.RESOLVED,
            InvestigationStatus.EXHAUSTED,
            InvestigationStatus.BLOCKED,
        ],
    )
    def test_loop_run_on_terminal_state_executes_zero_tools(self, terminal_status):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Should not execute",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=5)

        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=terminal_status,
        )

        final_state = loop.run(state)
        assert len(final_state.actions_taken) == 0
        assert len(final_state.evidence) == 0
        assert final_state.investigation_status == terminal_status
