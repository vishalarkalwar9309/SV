"""Deterministic Investigation Baseline for TRACE 2.0.

Provides a deterministic test/baseline harness demonstrating:
Incident → Initial Hypothesis → Action → Tool Execution → Evidence Creation
→ Evaluation → Hypothesis State Update → Deterministic Pivot → Final Resolution.

NOTE: This is a test baseline harness, NOT the autonomous agent or LLM planner.
"""

from __future__ import annotations

from trace.engine.decision_trace import DecisionTraceStep
from trace.engine.evaluator import EvaluationRelationship, EvidenceEvaluator
from trace.engine.evidence_manager import EvidenceManager
from trace.engine.hypothesis_manager import HypothesisManager
from trace.engine.state import InvestigationState, InvestigationStatus
from trace.models.action import AgentAction
from trace.models.hypothesis import Hypothesis, HypothesisStatus
from trace.simulator.models import ScenarioData
from trace.tools.fetch_commits import FetchRecentCommitsTool
from trace.tools.grep_logs import GrepLogsTool
from trace.tools.query_metrics import QueryMetricsTool
from typing import Any

from pydantic import BaseModel, ConfigDict


class DeterministicBaselineResult(BaseModel):
    """Output of running the deterministic baseline investigation harness."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    state: InvestigationState
    supported_hypothesis: Hypothesis | None = None
    disproven_hypotheses: list[Hypothesis] = []
    is_resolved: bool = False


def run_deterministic_baseline(scenario: ScenarioData) -> DeterministicBaselineResult:
    """Run the deterministic investigation flow for the checkout_502 scenario.

    Demonstrates:
    1. Register H1 (Database Overload)
    2. Query checkout-db metrics → CPU 12.3%, memory 34.1%
    3. Evaluate metrics → CONTRADICTS (-3) → H1 disproven
    4. Introduce H2 (Checkout Connection Leak)
    5. Query logs for checkout connection timeouts → SUPPORTS (+2)
    6. Fetch recent checkout commits:
       - If tool succeeds: Commit a1b2c3d4 diff confirms leak → SUPPORTS (+3) → H2 supported
       - If tool fails (e.g. 503): Tool failure recorded as NEUTRAL (0) → H2 not supported
    """
    evaluator = EvidenceEvaluator()
    evidence_mgr = EvidenceManager()
    hypothesis_mgr = HypothesisManager(evidence_store=evidence_mgr.store)

    # Initialize tools connected to simulator scenario
    metrics_tool = QueryMetricsTool(scenario)
    logs_tool = GrepLogsTool(scenario)
    commits_tool = FetchRecentCommitsTool(scenario)

    state = InvestigationState(
        incident=scenario.incident,
        investigation_status=InvestigationStatus.ACTIVE,
    )

    # =========================================================================
    # Step 1: Initial Hypothesis H1 = Database Overload
    # =========================================================================
    h1 = hypothesis_mgr.create("Database overload", hypothesis_id="hyp-db-overload")
    state.hypotheses.append(h1)
    state.current_hypothesis_id = h1.id
    hypothesis_mgr.update_status(h1.id, HypothesisStatus.INVESTIGATING)

    action_1 = AgentAction(
        tool_name=metrics_tool.name,
        params={"service": "checkout-db"},
        purpose="Check if database is overloaded to test H1",
        hypothesis_id=h1.id,
    )
    state.actions_taken.append(action_1)

    result_1 = metrics_tool.execute(service="checkout-db")
    ev_1 = evidence_mgr.record_tool_result(
        source_tool=metrics_tool.name,
        query=action_1.params,
        success=result_1.success,
        raw_data=result_1.data,
        error_message=result_1.error,
    )
    state.evidence.append(ev_1)

    eval_1 = evaluator.evaluate(h1, ev_1)
    if eval_1.relationship == EvaluationRelationship.CONTRADICTS:
        hypothesis_mgr.attach_contradicting_evidence(h1.id, ev_1.id)
    elif eval_1.relationship == EvaluationRelationship.SUPPORTS:
        hypothesis_mgr.attach_supporting_evidence(h1.id, ev_1.id)

    summary_1 = evaluator.aggregate(h1.id, [eval_1])
    hypothesis_mgr.update_status(
        h1.id,
        summary_1.recommended_status,
        disproval_reason=eval_1.reason,
    )

    cpu_val: Any = (result_1.data or {}).get("metrics", {}).get("cpu_percent", "N/A")
    mem_val: Any = (result_1.data or {}).get("metrics", {}).get("memory_percent", "N/A")
    trace_step_1 = DecisionTraceStep(
        step_number=1,
        action=metrics_tool.name,
        purpose=action_1.purpose,
        observation=f"CPU {cpu_val}%, memory {mem_val}% (healthy)",
        evaluation=f"{eval_1.relationship.value.upper()} ({eval_1.score_delta})",
        hypothesis_status=h1.status.value,
        next_action="Investigate alternative hypothesis: Checkout connection leak",
    )
    state.decision_trace.append(trace_step_1)

    # =========================================================================
    # Step 2: Alternative Hypothesis H2 = Checkout Connection Leak
    # =========================================================================
    h2 = hypothesis_mgr.create(
        "Checkout connection leak", hypothesis_id="hyp-conn-leak"
    )
    state.hypotheses.append(h2)
    state.current_hypothesis_id = h2.id
    hypothesis_mgr.update_status(h2.id, HypothesisStatus.INVESTIGATING)

    action_2 = AgentAction(
        tool_name=logs_tool.name,
        params={"service": "checkout", "pattern": "connection"},
        purpose="Search for connection pool errors in checkout service",
        hypothesis_id=h2.id,
    )
    state.actions_taken.append(action_2)

    result_2 = logs_tool.execute(service="checkout", pattern="connection")
    ev_2 = evidence_mgr.record_tool_result(
        source_tool=logs_tool.name,
        query=action_2.params,
        success=result_2.success,
        raw_data=result_2.data,
        error_message=result_2.error,
    )
    state.evidence.append(ev_2)

    eval_2 = evaluator.evaluate(h2, ev_2)
    if eval_2.relationship == EvaluationRelationship.SUPPORTS:
        hypothesis_mgr.attach_supporting_evidence(h2.id, ev_2.id)
    elif eval_2.relationship == EvaluationRelationship.CONTRADICTS:
        hypothesis_mgr.attach_contradicting_evidence(h2.id, ev_2.id)

    trace_step_2 = DecisionTraceStep(
        step_number=2,
        action=logs_tool.name,
        purpose=action_2.purpose,
        observation="Connection pool exhausted (20/20 in use) and acquisition timeouts",
        evaluation=f"{eval_2.relationship.value.upper()} ({eval_2.score_delta})",
        hypothesis_status=h2.status.value,
        next_action="Inspect recent commits for connection handling changes",
    )
    state.decision_trace.append(trace_step_2)

    # =========================================================================
    # Step 3: Inspect Commits for Root-Cause Confirmation
    # =========================================================================
    action_3 = AgentAction(
        tool_name=commits_tool.name,
        params={"service": "checkout", "limit": 5},
        purpose="Inspect recent commits for connection handling changes",
        hypothesis_id=h2.id,
    )
    state.actions_taken.append(action_3)

    result_3 = commits_tool.execute(service="checkout", limit=5)
    ev_3 = evidence_mgr.record_tool_result(
        source_tool=commits_tool.name,
        query=action_3.params,
        success=result_3.success,
        raw_data=result_3.data,
        error_message=result_3.error,
    )
    state.evidence.append(ev_3)

    eval_3 = evaluator.evaluate(h2, ev_3)
    if result_3.success:
        if eval_3.relationship == EvaluationRelationship.SUPPORTS:
            hypothesis_mgr.attach_supporting_evidence(h2.id, ev_3.id)
        elif eval_3.relationship == EvaluationRelationship.CONTRADICTS:
            hypothesis_mgr.attach_contradicting_evidence(h2.id, ev_3.id)

        summary_2 = evaluator.aggregate(h2.id, [eval_2, eval_3])
        hypothesis_mgr.update_status(h2.id, summary_2.recommended_status)
        state.investigation_status = (
            InvestigationStatus.RESOLVED
            if summary_2.recommended_status == HypothesisStatus.SUPPORTED
            else InvestigationStatus.ACTIVE
        )

        trace_step_3 = DecisionTraceStep(
            step_number=3,
            action=commits_tool.name,
            purpose=action_3.purpose,
            observation=(
                "Commit a1b2c3d4 modified connection release logic: "
                "connections are no longer explicitly closed"
            ),
            evaluation=f"{eval_3.relationship.value.upper()} ({eval_3.score_delta})",
            hypothesis_status=h2.status.value,
            next_action="Investigation complete: root cause confirmed",
        )
    else:
        # Tool failure case: e.g. 503
        summary_2 = evaluator.aggregate(h2.id, [eval_2, eval_3])
        # h2 is not supported; tool failure is neutral
        state.investigation_status = InvestigationStatus.BLOCKED

        trace_step_3 = DecisionTraceStep(
            step_number=3,
            action=commits_tool.name,
            purpose=action_3.purpose,
            observation=f"Tool execution failed: {result_3.error}",
            evaluation=f"{eval_3.relationship.value.upper()} ({eval_3.score_delta})",
            hypothesis_status=h2.status.value,
            next_action="Commit evidence unavailable due to tool failure",
        )

    state.decision_trace.append(trace_step_3)

    supported = hypothesis_mgr.get_supported()
    final_supported = supported[0] if supported else None

    return DeterministicBaselineResult(
        state=state,
        supported_hypothesis=final_supported,
        disproven_hypotheses=hypothesis_mgr.get_disproven(),
        is_resolved=state.investigation_status == InvestigationStatus.RESOLVED,
    )
