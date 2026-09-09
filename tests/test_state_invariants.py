"""Adversarial and invariant tests for State, Context Isolation, Trace, and Secrets.

Covers:
- Category M: Ground Truth Isolation (no leakage into planner context or trace)
- Category O: State Consistency (Pydantic validation and integrity across adversarial cases)
- Category P: Decision Trace Integrity (structured fields, strictly ordered, no CoT)
- Category R: Secret Safety (API key / fake secret redaction in errors and traces)
- Invariants 7, 9, 10
"""

from __future__ import annotations

from trace.agent.controller import AgentController
from trace.agent.investigation_loop import AutonomousInvestigationLoop
from trace.agent.llm_planner import LLMPlanner, PlannerError, build_planner_context
from trace.agent.planner import MockPlanner, PlannedAction
from trace.agent.registry import ToolRegistry
from trace.engine.state import InvestigationState, InvestigationStatus
from trace.models.hypothesis import Hypothesis
from trace.models.incident import Incident, Severity
from trace.simulator.models import ToolFailureConfig
from trace.simulator.scenarios.checkout_502 import create as create_checkout_scenario
from trace.tools.grep_logs import GrepLogsTool
from trace.tools.query_metrics import QueryMetricsTool
from unittest.mock import MagicMock

import pytest


class TestCategoryMGroundTruthIsolation:
    """Category M: Complete isolation of internal ground truth root cause."""

    def test_planner_context_never_exposes_ground_truth_root_cause(self):
        secret_root_cause = "CLASSIFIED_ROOT_CAUSE_SECRET_12345"
        incident = Incident(
            id="inc-test-gt",
            title="Checkout service degradation",
            service="checkout",
            severity=Severity.CRITICAL,
            initial_observation="502 errors observed on /pay",
            ground_truth_root_cause=secret_root_cause,
        )
        state = InvestigationState(
            incident=incident,
            hypotheses=[
                Hypothesis(id="h1", statement="Database saturation"),
                Hypothesis(id="h2", statement="Memory leak"),
            ],
            investigation_status=InvestigationStatus.ACTIVE,
        )

        tools = [{"name": "query_metrics", "description": "query", "parameters_schema": {}}]
        context = build_planner_context(state, tools)
        context_json = context.model_dump_json()

        # 1. Field name must not exist in context schema
        assert "ground_truth_root_cause" not in context_json

        # 2. Secret root cause string must not appear anywhere in context
        assert secret_root_cause not in context_json

        # 3. Incident dictionary inside context should only have approved safe fields
        incident_dict = context.incident
        assert set(incident_dict.keys()) == {
            "id",
            "title",
            "service",
            "severity",
            "initial_observation",
            "timestamp",
        }

    def test_decision_trace_never_contains_unobserved_ground_truth(self):
        secret_root_cause = "INTERNAL_POSTGRES_POOL_STARVATION_XYZ"
        incident = Incident(
            id="inc-gt-trace",
            title="Checkout errors",
            service="checkout",
            severity=Severity.HIGH,
            initial_observation="HTTP 502",
            ground_truth_root_cause=secret_root_cause,
        )
        state = InvestigationState(
            incident=incident,
            investigation_status=InvestigationStatus.ACTIVE,
        )

        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Investigate metrics",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=1)

        final_state = loop.run(state)
        for trace_step in final_state.decision_trace:
            dumped = trace_step.model_dump_json()
            assert secret_root_cause not in dumped


class TestCategoryPDecisionTraceIntegrity:
    """Category P: Decision trace integrity, ordering, and absence of chain-of-thought."""

    def test_decision_trace_steps_are_strictly_sequential(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario), GrepLogsTool(scenario)])
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Step 1",
            ),
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": "timeout"},
                purpose="Step 2",
            ),
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=2)

        state = InvestigationState(incident=scenario.incident)
        final_state = loop.run(state)

        assert len(final_state.decision_trace) == 2
        for i, step in enumerate(final_state.decision_trace):
            # Step numbering must be 1-indexed and strictly sequential
            assert step.step_number == i + 1
            # Required non-empty fields
            assert step.action != ""
            assert step.purpose != ""
            assert step.observation != ""
            assert step.evaluation != ""
            assert step.next_action != ""

    def test_decision_trace_contains_no_chain_of_thought_fields(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Examine DB",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=1)

        state = InvestigationState(incident=scenario.incident)
        final_state = loop.run(state)

        for step in final_state.decision_trace:
            d = step.model_dump()
            assert "chain_of_thought" not in d
            assert "thought" not in d
            assert "reasoning_steps" not in d
            assert "hidden_reasoning" not in d


class TestCategoryRSecretSafety:
    """Category R: Secret and API key redaction in error messages, traces, and evidence."""

    def test_fake_api_key_redacted_from_llm_planner_errors(self, monkeypatch):
        fake_api_key = "fake_gemini_api_key_abc_123_xyz_789"
        monkeypatch.setenv("GOOGLE_API_KEY", fake_api_key)

        mock_client = MagicMock()
        # Simulate Google API client raising an exception containing the API key in the error string
        mock_client.models.generate_content.side_effect = RuntimeError(
            f"Google API call failed with key {fake_api_key}: 403 Forbidden"
        )

        planner = LLMPlanner(client=mock_client, model_name="gemini-3.6-flash")
        scenario = create_checkout_scenario()
        state = InvestigationState(incident=scenario.incident)

        with pytest.raises(PlannerError) as exc_info:
            planner.plan_next_action(state, [])

        err_str = str(exc_info.value)
        # Secret MUST NOT appear in the exception string
        assert fake_api_key not in err_str
        # Secret MUST be replaced with [REDACTED]
        assert "[REDACTED]" in err_str

    def test_redacted_planner_error_does_not_leak_into_decision_trace(self, monkeypatch):
        fake_api_key = "fake_secret_key_dont_leak_me_456"
        monkeypatch.setenv("GOOGLE_API_KEY", fake_api_key)

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError(
            f"Authentication failed for key {fake_api_key}"
        )

        planner = LLMPlanner(client=mock_client, model_name="gemini-3.6-flash")
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=1)

        state = InvestigationState(incident=scenario.incident)
        final_state = loop.run(state)

        assert final_state.investigation_status == InvestigationStatus.BLOCKED
        assert len(final_state.decision_trace) == 1
        trace_json = final_state.decision_trace[0].model_dump_json()

        assert fake_api_key not in trace_json
        assert "[REDACTED]" in trace_json


class TestCategoryOStateConsistencyAndInvariants:
    """Category O & Invariant 10: State consistency and validation across operations."""

    def test_state_remains_pydantic_valid_after_tool_failure(self):
        scenario = create_checkout_scenario(
            tool_failures={
                "query_metrics": ToolFailureConfig(
                    enabled=True,
                    error_message="HTTP 503 Backend Failure",
                )
            }
        )
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        h = Hypothesis(id="h1", statement="DB overload")
        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=[h],
            investigation_status=InvestigationStatus.ACTIVE,
        )
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Attempt query",
                hypothesis_id="h1",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=1)

        final_state = loop.run(state)

        # Full Pydantic re-validation
        revalidated = InvestigationState.model_validate(final_state.model_dump())
        assert revalidated.investigation_status == InvestigationStatus.EXHAUSTED
        assert revalidated.incident == scenario.incident
        assert len(revalidated.evidence) == 1
        assert revalidated.evidence[0].tool_succeeded is False

    def test_state_remains_pydantic_valid_after_validation_error(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=InvestigationStatus.ACTIVE,
        )
        planner = MockPlanner([
            PlannedAction(
                tool_name="unregistered_tool",
                parameters={},
                purpose="Invalid tool",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=1)

        final_state = loop.run(state)

        revalidated = InvestigationState.model_validate(final_state.model_dump())
        assert revalidated.investigation_status == InvestigationStatus.BLOCKED
        assert len(revalidated.actions_taken) == 0
        assert len(revalidated.decision_trace) == 1

    def test_no_orphaned_evidence_references_in_hypotheses(self):
        """All evidence IDs on hypotheses must exist in state.evidence."""
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])
        h = Hypothesis(id="h1", statement="Database overload")
        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=[h],
            investigation_status=InvestigationStatus.ACTIVE,
        )
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Check DB",
                hypothesis_id="h1",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=1)

        final_state = loop.run(state)

        evidence_ids = {ev.id for ev in final_state.evidence}
        for hyp in final_state.hypotheses:
            for sup_id in hyp.supporting_evidence_ids:
                assert sup_id in evidence_ids, f"Orphaned supporting evidence {sup_id}"
            for con_id in hyp.contradicting_evidence_ids:
                assert con_id in evidence_ids, f"Orphaned contradicting evidence {con_id}"

    def test_incident_remains_immutable_throughout_investigation(self):
        scenario = create_checkout_scenario()
        orig_incident = scenario.incident.model_copy(deep=True)
        registry = ToolRegistry([QueryMetricsTool(scenario), GrepLogsTool(scenario)])
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Step 1",
            ),
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": "error"},
                purpose="Step 2",
            ),
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=2)

        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=InvestigationStatus.ACTIVE,
        )

        final_state = loop.run(state)
        # Incident properties must remain identical to original
        assert final_state.incident.id == orig_incident.id
        assert final_state.incident.title == orig_incident.title
        assert final_state.incident.severity == orig_incident.severity
        assert final_state.incident.service == orig_incident.service
        assert final_state.incident.initial_observation == orig_incident.initial_observation
        assert final_state.incident.ground_truth_root_cause == orig_incident.ground_truth_root_cause
