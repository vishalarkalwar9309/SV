"""Comprehensive deterministic tests for LLMPlanner using injectable fake client."""

from __future__ import annotations

import json
from trace.agent.controller import ActionValidationError, AgentController
from trace.agent.llm_planner import (
    LLMPlanner,
    PlannerError,
    build_planner_context,
)
from trace.agent.planner import PlannedAction
from trace.agent.registry import ToolRegistry
from trace.engine.state import InvestigationState, InvestigationStatus
from trace.models.evidence import EvidenceItem
from trace.models.hypothesis import Hypothesis, HypothesisStatus
from trace.simulator.scenarios.checkout_502 import create as create_checkout_scenario
from trace.tools.query_metrics import QueryMetricsTool
from typing import Any

import pytest


class FakeGeminiResponse:
    """Mock Gemini API response object."""

    def __init__(self, text: str | None = None, parsed: Any = None) -> None:
        self.text = text
        self.parsed = parsed


class FakeGeminiClient:
    """Mock Gemini client implementing the models.generate_content interface."""

    def __init__(self, response: Any = None) -> None:
        self.response = response
        self.last_contents: str | None = None
        self.last_config: Any = None
        self.call_count: int = 0

    class Models:
        def __init__(self, outer: FakeGeminiClient) -> None:
            self._outer = outer

        def generate_content(
            self,
            model: str,
            contents: str,
            config: Any = None,
        ) -> Any:
            self._outer.call_count += 1
            self._outer.last_contents = contents
            self._outer.last_config = config

            if isinstance(self._outer.response, Exception):
                raise self._outer.response
            return self._outer.response

    @property
    def models(self) -> Models:
        return self.Models(self)


class TestLLMPlannerParsing:
    def test_valid_structured_action_from_json_text(self):
        fake_json = json.dumps({
            "tool_name": "query_metrics",
            "parameters": {"service": "checkout-db"},
            "purpose": "Check CPU and memory usage",
            "hypothesis_id": "h-01",
        })
        client = FakeGeminiClient(response=FakeGeminiResponse(text=fake_json))
        planner = LLMPlanner(client=client)

        scenario = create_checkout_scenario()
        state = InvestigationState(incident=scenario.incident)

        action = planner.plan_next_action(state, [])
        assert action is not None
        assert action.tool_name == "query_metrics"
        assert action.parameters == {"service": "checkout-db"}
        assert action.purpose == "Check CPU and memory usage"
        assert action.hypothesis_id == "h-01"

    def test_valid_structured_action_from_parsed_object(self):
        planned = PlannedAction(
            tool_name="grep_logs",
            parameters={"service": "checkout", "pattern": "error"},
            purpose="Search for 500 errors",
            hypothesis_id="h-02",
        )
        client = FakeGeminiClient(response=FakeGeminiResponse(parsed=planned))
        planner = LLMPlanner(client=client)

        scenario = create_checkout_scenario()
        state = InvestigationState(incident=scenario.incident)

        action = planner.plan_next_action(state, [])
        assert action == planned

    def test_invalid_json_raises_planner_error(self):
        client = FakeGeminiClient(response=FakeGeminiResponse(text="NOT_VALID_JSON"))
        planner = LLMPlanner(client=client)
        scenario = create_checkout_scenario()
        state = InvestigationState(incident=scenario.incident)

        with pytest.raises(PlannerError, match="valid JSON"):
            planner.plan_next_action(state, [])

    def test_missing_required_fields_raises_planner_error(self):
        # Missing "purpose"
        bad_json = json.dumps({"tool_name": "query_metrics", "parameters": {}})
        client = FakeGeminiClient(response=FakeGeminiResponse(text=bad_json))
        planner = LLMPlanner(client=client)
        scenario = create_checkout_scenario()
        state = InvestigationState(incident=scenario.incident)

        with pytest.raises(PlannerError, match="validation"):
            planner.plan_next_action(state, [])

    def test_empty_or_null_response_returns_none(self):
        client = FakeGeminiClient(response=FakeGeminiResponse(text="null"))
        planner = LLMPlanner(client=client)
        scenario = create_checkout_scenario()
        state = InvestigationState(incident=scenario.incident)

        action = planner.plan_next_action(state, [])
        assert action is None

    def test_api_exception_propagates_as_planner_error(self):
        client = FakeGeminiClient(response=TimeoutError("API connection timed out"))
        planner = LLMPlanner(client=client)
        scenario = create_checkout_scenario()
        state = InvestigationState(incident=scenario.incident)

        with pytest.raises(PlannerError, match="LLM generation failed"):
            planner.plan_next_action(state, [])

    def test_missing_api_key_raises_planner_error(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        planner = LLMPlanner()  # No key, no client
        scenario = create_checkout_scenario()
        state = InvestigationState(incident=scenario.incident)

        with pytest.raises(PlannerError, match="Google GenAI client is not configured"):
            planner.plan_next_action(state, [])


class TestPlannerContextIntegrity:
    def test_ground_truth_never_in_context(self):
        scenario = create_checkout_scenario()
        assert scenario.incident.ground_truth_root_cause is not None

        state = InvestigationState(incident=scenario.incident)
        context = build_planner_context(state, [])
        context_dump = json.dumps(context.model_dump())

        assert "ground_truth_root_cause" not in context_dump
        assert "Connection leak in checkout deploy" not in context_dump
        assert "a1b2c3d4" not in context_dump

    def test_disproven_hypothesis_represented_in_context(self):
        scenario = create_checkout_scenario()
        h_disproven = Hypothesis(
            id="h-db",
            statement="Database overload",
            status=HypothesisStatus.DISPROVEN,
            disproval_reason="Metrics show normal CPU usage (12.3%)",
        )
        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=[h_disproven],
        )

        context = build_planner_context(state, [])
        assert len(context.hypotheses) == 1
        h_info = context.hypotheses[0]
        assert h_info["status"] == "disproven"
        assert h_info["is_terminal"] is True
        assert "12.3%" in h_info["disproval_reason"]

    def test_failed_tool_represented_as_unavailable_not_root_cause(self):
        scenario = create_checkout_scenario()
        state = InvestigationState(incident=scenario.incident)
        ev_failed = EvidenceItem(
            id="ev-fail",
            source_tool="fetch_recent_commits",
            query={"service": "checkout"},
            raw_data={},
            tool_succeeded=False,
            error_message="HTTP 503: Service Unavailable",
        )
        state.evidence.append(ev_failed)

        context = build_planner_context(state, [])
        assert len(context.evidence_summary) == 1
        ev_info = context.evidence_summary[0]
        assert ev_info["tool_succeeded"] is False
        assert ev_info["status"] == "TOOL_UNAVAILABLE"
        assert "503" in ev_info["error_message"]


class TestPrimaryScenarioControllerIntegration:
    """Requirement 10: Primary scenario integration test with mock LLM planner."""

    def test_mock_llm_planner_drives_controller_step(self):
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])

        h1 = Hypothesis(id="h1-db", statement="Database overload")
        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=[h1],
            current_hypothesis_id="h1-db",
            investigation_status=InvestigationStatus.ACTIVE,
        )

        # Mock LLM returns query_metrics action
        fake_json = json.dumps({
            "tool_name": "query_metrics",
            "parameters": {"service": "checkout-db"},
            "purpose": "Inspect checkout database CPU and memory metrics to evaluate H1",
            "hypothesis_id": "h1-db",
        })
        client = FakeGeminiClient(response=FakeGeminiResponse(text=fake_json))
        planner = LLMPlanner(client=client)

        controller = AgentController(planner=planner, tool_registry=registry)
        step_result = controller.step(state)

        # 1. Planner was called and sent prompt
        assert client.call_count == 1
        assert client.last_contents is not None
        assert "ground_truth_root_cause" not in client.last_contents

        # 2. Action was validated and executed by Controller
        assert step_result is not None
        assert step_result.action.tool_name == "query_metrics"

        # 3. Evidence was created from real tool execution (CPU 12.3%)
        assert len(state.evidence) == 1
        assert state.evidence[0].raw_data["metrics"]["cpu_percent"] == 12.3

        # 4. H1 evaluated and updated to DISPROVEN
        h1_updated = controller.hypothesis_manager.get("h1-db")
        assert h1_updated is not None
        assert h1_updated.status == HypothesisStatus.DISPROVEN

        # 5. Planner did NOT execute the tool directly
        assert len(state.actions_taken) == 1

    def test_controller_rejects_unavailable_tool_from_llm(self):
        """Planner proposes a tool not in the registry; controller rejects safely."""
        scenario = create_checkout_scenario()
        registry = ToolRegistry([QueryMetricsTool(scenario)])  # Only query_metrics

        state = InvestigationState(incident=scenario.incident)

        # Mock LLM proposes unregistered tool "reboot_server"
        fake_json = json.dumps({
            "tool_name": "reboot_server",
            "parameters": {"server": "db-1"},
            "purpose": "Attempt server reboot",
        })
        client = FakeGeminiClient(response=FakeGeminiResponse(text=fake_json))
        planner = LLMPlanner(client=client)
        controller = AgentController(planner=planner, tool_registry=registry)

        with pytest.raises(ActionValidationError, match="Unknown tool"):
            controller.step(state)

        # No actions executed, no evidence created
        assert len(state.actions_taken) == 0
        assert len(state.evidence) == 0

    def test_reproducibility_with_same_state_and_mock(self):
        """Requirement 9: Same mock model + same state produce equivalent PlannedAction."""
        scenario = create_checkout_scenario()
        fake_json = json.dumps({
            "tool_name": "grep_logs",
            "parameters": {"service": "checkout", "pattern": "connection"},
            "purpose": "Search connection pool logs",
            "hypothesis_id": "h-02",
        })

        client_1 = FakeGeminiClient(response=FakeGeminiResponse(text=fake_json))
        client_2 = FakeGeminiClient(response=FakeGeminiResponse(text=fake_json))

        state_1 = InvestigationState(incident=scenario.incident)
        state_2 = InvestigationState(incident=scenario.incident)

        action_1 = LLMPlanner(client=client_1).plan_next_action(state_1, [])
        action_2 = LLMPlanner(client=client_2).plan_next_action(state_2, [])

        assert action_1 == action_2
