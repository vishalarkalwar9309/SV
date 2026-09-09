"""Adversarial and integrity tests for Evidence, EvidenceManager, and Evaluator.

Covers:
- Category B: Missing Data (empty logs, missing metrics, empty commits, no fabrication)
- Category N: Evidence Integrity (provenance, tool failure safety, duplicate handling)
- Invariant 2: No successful evidence exists without successful tool execution
- Invariant 3: Failed tool call cannot provide root-cause support or contradiction
- Invariant 8: Hypothesis evidence references remain valid and resolvable
"""

from __future__ import annotations

from trace.agent.controller import AgentController
from trace.agent.investigation_loop import AutonomousInvestigationLoop
from trace.agent.planner import MockPlanner, PlannedAction
from trace.agent.registry import ToolRegistry
from trace.engine.evaluator import (
    EvaluationRelationship,
    EvidenceEvaluator,
    EvidenceWeight,
)
from trace.engine.evidence_manager import (
    DuplicateEvidenceError,
    EvidenceManager,
    EvidenceNotFoundError,
    InvalidEvidenceClassificationError,
)
from trace.engine.hypothesis_manager import HypothesisManager
from trace.engine.state import InvestigationState, InvestigationStatus
from trace.models.evidence import EvidenceItem, EvidenceStore
from trace.models.hypothesis import Hypothesis, HypothesisStatus
from trace.simulator.scenarios.checkout_502 import create as create_checkout_scenario
from trace.tools.grep_logs import GrepLogsTool
from trace.tools.query_metrics import QueryMetricsTool

import pytest


class TestCategoryBMissingData:
    """Category B: Handling missing, empty, or incomplete data without fabrication."""

    def test_empty_log_matches_does_not_support_hypothesis(self):
        evaluator = EvidenceEvaluator()
        hypothesis = Hypothesis(id="h1", statement="Checkout connection leak")
        empty_log_evidence = EvidenceItem(
            id="ev-empty-logs",
            source_tool="grep_logs",
            query={"service": "checkout", "pattern": "nonexistent_error_pattern"},
            tool_succeeded=True,
            raw_data={"service": "checkout", "pattern": "nonexistent_error_pattern", "matches": []},
        )

        result = evaluator.evaluate(hypothesis, empty_log_evidence)

        # Non-probative empty logs receive neutral evaluation
        assert result.relationship == EvaluationRelationship.NEUTRAL
        assert result.score_delta == EvidenceWeight.NEUTRAL
        assert result.rule_id == "RULE_LOGS_NEUTRAL"

        # No fabricated matches
        assert empty_log_evidence.raw_data["matches"] == []

        # Aggregation shows INSUFFICIENT
        summary = evaluator.aggregate(hypothesis.id, [result])
        assert summary.recommended_status == HypothesisStatus.INSUFFICIENT
        assert summary.total_score == 0

    def test_empty_commits_does_not_support_hypothesis(self):
        evaluator = EvidenceEvaluator()
        hypothesis = Hypothesis(id="h1", statement="Checkout connection leak")
        empty_commit_evidence = EvidenceItem(
            id="ev-empty-commits",
            source_tool="fetch_recent_commits",
            query={"service": "checkout", "limit": 5},
            tool_succeeded=True,
            raw_data={"service": "checkout", "commits": []},
        )

        result = evaluator.evaluate(hypothesis, empty_commit_evidence)

        assert result.relationship == EvaluationRelationship.NEUTRAL
        assert result.score_delta == EvidenceWeight.NEUTRAL
        assert result.rule_id == "RULE_COMMITS_NEUTRAL"

        summary = evaluator.aggregate(hypothesis.id, [result])
        assert summary.recommended_status == HypothesisStatus.INSUFFICIENT
        assert summary.total_score == 0

    def test_missing_metric_keys_treated_as_neutral(self):
        evaluator = EvidenceEvaluator()
        hypothesis = Hypothesis(id="h1", statement="Database overload")
        # Metrics raw data missing cpu_percent and memory_percent
        incomplete_metrics_evidence = EvidenceItem(
            id="ev-incomplete-metrics",
            source_tool="query_metrics",
            query={"service": "checkout-db"},
            tool_succeeded=True,
            raw_data={"service": "checkout-db", "metrics": {"other_counter": 123}},
        )

        result = evaluator.evaluate(hypothesis, incomplete_metrics_evidence)

        assert result.relationship == EvaluationRelationship.NEUTRAL
        assert result.score_delta == EvidenceWeight.NEUTRAL
        assert result.rule_id == "RULE_METRICS_NEUTRAL"

    def test_explicit_critical_missing_evidence_penalty(self):
        evaluator = EvidenceEvaluator()
        hypothesis = Hypothesis(id="h1", statement="Checkout connection leak")

        result = evaluator.evaluate_missing_evidence(
            hypothesis,
            missing_source="checkout_db_telemetry",
        )

        assert result.score_delta == EvidenceWeight.CRITICAL_MISSING
        assert result.score_delta == -1
        assert "Critical evidence missing" in result.reason

    def test_investigation_loop_with_empty_results_remains_unsupported(self):
        """When tool queries return no probative matches, hypothesis is never marked SUPPORTED."""
        scenario = create_checkout_scenario()
        registry = ToolRegistry([GrepLogsTool(scenario)])
        h = Hypothesis(id="h1", statement="Checkout connection leak")
        state = InvestigationState(
            incident=scenario.incident,
            hypotheses=[h],
            investigation_status=InvestigationStatus.ACTIVE,
        )

        # Propose search with pattern that matches nothing
        planner = MockPlanner([
            PlannedAction(
                tool_name="grep_logs",
                parameters={
                    "service": "checkout",
                    "pattern": "definitely_no_such_error_pattern_xyz",
                },
                purpose="Search for non-existent pattern",
                hypothesis_id="h1",
            )
        ])
        controller = AgentController(planner=planner, tool_registry=registry)
        loop = AutonomousInvestigationLoop(controller=controller, max_steps=2)

        final_state = loop.run(state)

        # Hypothesis must NOT be marked SUPPORTED
        assert h.status != HypothesisStatus.SUPPORTED
        assert len(h.supporting_evidence_ids) == 0
        assert final_state.investigation_status == InvestigationStatus.EXHAUSTED
        # Decision trace accurately records observation
        assert len(final_state.decision_trace) == 1
        assert "0 log match" in final_state.decision_trace[0].observation.lower()
        assert "NEUTRAL" in final_state.decision_trace[0].evaluation


class TestCategoryNEvidenceIntegrity:
    """Category N: Evidence provenance, store integrity, and validation constraints."""

    def test_duplicate_evidence_id_rejected_by_evidence_manager(self):
        store = EvidenceStore()
        manager = EvidenceManager(store)
        item = EvidenceItem(
            id="ev-dup-1",
            source_tool="query_metrics",
            query={"service": "checkout"},
            tool_succeeded=True,
            raw_data={},
        )
        manager.add(item)

        with pytest.raises(DuplicateEvidenceError, match="already exists"):
            manager.add(item)

    def test_duplicate_evidence_id_rejected_by_evidence_store(self):
        store = EvidenceStore()
        item = EvidenceItem(
            id="ev-store-dup",
            source_tool="grep_logs",
            query={},
            tool_succeeded=True,
            raw_data={},
        )
        store.add(item)

        with pytest.raises(ValueError, match="already exists"):
            store.add(item)

    def test_get_or_raise_nonexistent_evidence(self):
        manager = EvidenceManager()
        with pytest.raises(EvidenceNotFoundError, match="not found"):
            manager.get_or_raise("ev-nonexistent-404")

    def test_get_successful_and_get_failed_partition(self):
        manager = EvidenceManager()
        manager.record_tool_result("grep_logs", {}, success=True, evidence_id="s1")
        manager.record_tool_result(
            "fetch_recent_commits", {}, success=False, error_message="503", evidence_id="f1"
        )
        manager.record_tool_result("query_metrics", {}, success=True, evidence_id="s2")

        successful = manager.get_successful()
        failed = manager.get_failed()

        assert len(successful) == 2
        assert {item.id for item in successful} == {"s1", "s2"}
        assert len(failed) == 1
        assert failed[0].id == "f1"
        assert failed[0].error_message == "503"

    def test_list_all_preserves_deterministic_insertion_order(self):
        manager = EvidenceManager()
        ids = [f"ev-{i}" for i in range(10)]
        for ev_id in ids:
            manager.record_tool_result("grep_logs", {}, success=True, evidence_id=ev_id)

        all_items = manager.list_all()
        assert [item.id for item in all_items] == ids


class TestInvariant2NoSuccessfulEvidenceWithoutExecution:
    """Invariant 2: No successful evidence exists without a successful tool execution."""

    def test_invariant_evidence_items_match_executed_actions(self):
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

        state = InvestigationState(
            incident=scenario.incident,
            investigation_status=InvestigationStatus.ACTIVE,
        )

        final_state = loop.run(state)

        assert len(final_state.evidence) == len(final_state.actions_taken) == 2
        for action, ev in zip(final_state.actions_taken, final_state.evidence, strict=False):
            assert ev.source_tool == action.tool_name
            assert ev.tool_succeeded is True
            assert ev.timestamp is not None
            assert isinstance(ev.raw_data, dict)


class TestInvariant3FailedToolCallCannotProvideRootCauseEvidence:
    """Invariant 3: A failed tool call cannot provide root-cause support or contradiction."""

    def test_invariant_evidence_manager_rejects_failed_tool_classification(self):
        store = EvidenceStore()
        manager = EvidenceManager(store)
        failed_ev = manager.record_tool_result(
            source_tool="query_metrics",
            query={"service": "checkout"},
            success=False,
            error_message="HTTP 503 Backend Timeout",
            evidence_id="ev-fail-1",
        )

        with pytest.raises(InvalidEvidenceClassificationError, match="MUST NOT be classified"):
            manager.validate_root_cause_eligibility(failed_ev.id)

    def test_invariant_hypothesis_manager_rejects_attaching_failed_evidence(self):
        store = EvidenceStore()
        ev_manager = EvidenceManager(store)
        hyp_manager = HypothesisManager(evidence_store=ev_manager)

        failed_ev = ev_manager.record_tool_result(
            source_tool="fetch_recent_commits",
            query={},
            success=False,
            error_message="Connection refused",
            evidence_id="ev-failed-conn",
        )

        h = hyp_manager.create("Connection pool exhaustion", hypothesis_id="h-test")

        # Attempt to attach failed tool result as supporting evidence
        with pytest.raises(InvalidEvidenceClassificationError):
            hyp_manager.attach_supporting_evidence(h.id, failed_ev.id)

        # Attempt to attach failed tool result as contradicting evidence
        with pytest.raises(InvalidEvidenceClassificationError):
            hyp_manager.attach_contradicting_evidence(h.id, failed_ev.id)

        assert len(h.supporting_evidence_ids) == 0
        assert len(h.contradicting_evidence_ids) == 0

    def test_invariant_evaluator_scores_failed_tool_as_neutral_zero(self):
        evaluator = EvidenceEvaluator()
        hypothesis = Hypothesis(id="h1", statement="Database overload")
        failed_evidence = EvidenceItem(
            id="ev-500",
            source_tool="query_metrics",
            query={"service": "checkout-db"},
            tool_succeeded=False,
            error_message="HTTP 500 Internal Server Error",
            raw_data={},
        )

        result = evaluator.evaluate(hypothesis, failed_evidence)
        assert result.relationship == EvaluationRelationship.NEUTRAL
        assert result.score_delta == EvidenceWeight.NEUTRAL
        assert result.score_delta == 0
        assert result.rule_id == "RULE_TOOL_FAILURE"


class TestInvariant8HypothesisEvidenceReferencesRemainValid:
    """Invariant 8: Hypothesis evidence references remain valid and resolvable."""

    def test_invariant_evidence_references_resolve_to_real_items(self):
        store = EvidenceStore()
        ev_manager = EvidenceManager(store)
        hyp_manager = HypothesisManager(evidence_store=ev_manager)

        ev1 = ev_manager.record_tool_result("grep_logs", {}, success=True, evidence_id="ev-1")
        ev2 = ev_manager.record_tool_result(
            "fetch_recent_commits", {}, success=True, evidence_id="ev-2"
        )

        h = hyp_manager.create("Checkout connection leak", hypothesis_id="h-leak")
        hyp_manager.attach_supporting_evidence(h.id, ev1.id)
        hyp_manager.attach_contradicting_evidence(h.id, ev2.id)

        grouped = ev_manager.get_for_hypothesis(h)
        assert len(grouped["supporting"]) == 1
        assert grouped["supporting"][0].id == "ev-1"
        assert len(grouped["contradicting"]) == 1
        assert grouped["contradicting"][0].id == "ev-2"

    def test_nonexistent_evidence_reference_fails_validation(self):
        ev_manager = EvidenceManager()
        hyp_manager = HypothesisManager(evidence_store=ev_manager)
        h = hyp_manager.create("Test hypothesis", hypothesis_id="h-1")

        with pytest.raises(EvidenceNotFoundError):
            hyp_manager.attach_supporting_evidence(h.id, "nonexistent-evidence-id")
