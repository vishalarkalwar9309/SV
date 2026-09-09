"""Comprehensive tests for the deterministic end-to-end investigation baseline."""

from trace.engine.baseline import run_deterministic_baseline
from trace.engine.state import InvestigationStatus
from trace.models.hypothesis import HypothesisStatus
from trace.simulator.models import ToolFailureConfig
from trace.simulator.scenarios.checkout_502 import create as create_checkout_scenario


class TestPrimaryScenarioFlow:
    """Tests the primary checkout_502 red-herring and pivot investigation flow."""

    def test_primary_flow_red_herring_and_resolution(self):
        """H1 (DB Overload) is disproven, H2 (Connection Leak) is supported."""
        scenario = create_checkout_scenario()
        result = run_deterministic_baseline(scenario)

        # 1. Verification of H1 Disproval
        disproven = result.disproven_hypotheses
        assert len(disproven) == 1
        h1 = disproven[0]
        assert h1.statement == "Database overload"
        assert h1.status == HypothesisStatus.DISPROVEN
        assert len(h1.contradicting_evidence_ids) == 1
        assert len(h1.supporting_evidence_ids) == 0
        assert h1.disproval_reason is not None
        assert "below overload thresholds" in h1.disproval_reason

        # 2. Verification of H2 Support
        h2 = result.supported_hypothesis
        assert h2 is not None
        assert h2.statement == "Checkout connection leak"
        assert h2.status == HypothesisStatus.SUPPORTED
        assert len(h2.supporting_evidence_ids) == 2  # logs + commit
        assert len(h2.contradicting_evidence_ids) == 0

        # 3. Verification of State & Resolution
        assert result.is_resolved is True
        assert result.state.investigation_status == InvestigationStatus.RESOLVED
        assert len(result.state.evidence) == 3
        assert len(result.state.actions_taken) == 3

        # 4. Evidence reference integrity
        ev1_id = h1.contradicting_evidence_ids[0]
        ev_metric = result.state.get_evidence(ev1_id)
        assert ev_metric is not None
        assert ev_metric.source_tool == "query_metrics"
        assert ev_metric.raw_data["metrics"]["cpu_percent"] == 12.3

        ev2_id = h2.supporting_evidence_ids[0]
        ev_logs = result.state.get_evidence(ev2_id)
        assert ev_logs is not None
        assert ev_logs.source_tool == "grep_logs"

        ev3_id = h2.supporting_evidence_ids[1]
        ev_commits = result.state.get_evidence(ev3_id)
        assert ev_commits is not None
        assert ev_commits.source_tool == "fetch_recent_commits"
        assert ev_commits.raw_data["commits"][0]["hash"] == "a1b2c3d4"

    def test_decision_trace_generation(self):
        """Verify the structured decision trace reflects the audit trail."""
        scenario = create_checkout_scenario()
        result = run_deterministic_baseline(scenario)
        trace = result.state.decision_trace

        assert len(trace) == 3
        # Step 1: Metrics
        assert trace[0].step_number == 1
        assert trace[0].action == "query_metrics"
        assert "overload" in trace[0].purpose.lower()
        assert "CONTRADICTS (-3)" in trace[0].evaluation
        assert trace[0].hypothesis_status == "disproven"

        # Step 2: Logs
        assert trace[1].step_number == 2
        assert trace[1].action == "grep_logs"
        assert "SUPPORTS (2)" in trace[1].evaluation
        assert trace[1].hypothesis_status == "investigating"

        # Step 3: Commits
        assert trace[2].step_number == 3
        assert trace[2].action == "fetch_recent_commits"
        assert "SUPPORTS (3)" in trace[2].evaluation
        assert trace[2].hypothesis_status == "supported"

        # Ensure serializability
        dumped = [step.model_dump() for step in trace]
        assert len(dumped) == 3
        assert dumped[0]["action"] == "query_metrics"


class TestToolFailureFlow:
    """Tests simulated tool failures (e.g. HTTP 503) propagating safely."""

    def test_commit_tool_failure_scenario(self):
        scenario = create_checkout_scenario(
            tool_failures={
                "fetch_recent_commits": ToolFailureConfig(
                    enabled=True,
                    error_message="HTTP 503: Service Unavailable",
                )
            }
        )
        result = run_deterministic_baseline(scenario)

        # H1 should still be disproven
        assert len(result.disproven_hypotheses) == 1
        assert result.disproven_hypotheses[0].status == HypothesisStatus.DISPROVEN

        # H2 cannot become supported without confirmation; tool failed
        assert result.supported_hypothesis is None
        assert result.is_resolved is False
        assert result.state.investigation_status == InvestigationStatus.BLOCKED

        # Evidence record 3 must reflect the failed tool call
        ev_failed = result.state.evidence[2]
        assert ev_failed.source_tool == "fetch_recent_commits"
        assert ev_failed.tool_succeeded is False
        assert "503" in (ev_failed.error_message or "")
        assert ev_failed.raw_data == {}

        # Decision trace notes the tool failure
        trace_step_3 = result.state.decision_trace[2]
        assert "NEUTRAL (0)" in trace_step_3.evaluation
        assert "503" in trace_step_3.observation
        assert trace_step_3.hypothesis_status == "investigating"


class TestRootCauseSafety:
    """Verify flow does not access or rely on ground_truth_root_cause."""

    def test_sanitized_incident_flow(self):
        scenario = create_checkout_scenario()
        # Explicitly erase ground truth metadata from incident and scenario
        scenario.incident.ground_truth_root_cause = None
        scenario.ground_truth = None

        result = run_deterministic_baseline(scenario)

        assert result.is_resolved is True
        assert result.supported_hypothesis is not None
        assert result.supported_hypothesis.statement == "Checkout connection leak"
        assert len(result.disproven_hypotheses) == 1

    def test_ground_truth_property_trap(self):
        """Simulate an incident object where accessing ground_truth_root_cause raises."""
        from trace.models.incident import Incident, Severity

        class TrappedIncident(Incident):
            def __getattribute__(self, name: str):
                if name == "ground_truth_root_cause":
                    raise RuntimeError(
                        "TRAP: ground_truth_root_cause accessed during investigation!"
                    )
                return super().__getattribute__(name)

        trapped_incident = TrappedIncident(
            id="trap-01",
            title="Checkout 502s",
            service="checkout",
            severity=Severity.HIGH,
            initial_observation="502 spike",
        )
        trapped_scenario = create_checkout_scenario()
        trapped_scenario.incident = trapped_incident

        # The run should execute without triggering the trap
        result = run_deterministic_baseline(trapped_scenario)
        assert result.is_resolved is True


class TestDeterminism:
    def test_repeated_runs_produce_equivalent_results(self):
        scenario_a = create_checkout_scenario()
        scenario_b = create_checkout_scenario()

        result_a = run_deterministic_baseline(scenario_a)
        result_b = run_deterministic_baseline(scenario_b)

        # Both resolved
        assert result_a.is_resolved == result_b.is_resolved is True

        # Disproven hypotheses match
        assert len(result_a.disproven_hypotheses) == len(result_b.disproven_hypotheses) == 1
        assert (
            result_a.disproven_hypotheses[0].statement
            == result_b.disproven_hypotheses[0].statement
            == "Database overload"
        )
        assert (
            result_a.disproven_hypotheses[0].status
            == result_b.disproven_hypotheses[0].status
            == HypothesisStatus.DISPROVEN
        )

        # Supported hypothesis matches
        assert result_a.supported_hypothesis is not None
        assert result_b.supported_hypothesis is not None
        assert (
            result_a.supported_hypothesis.statement
            == result_b.supported_hypothesis.statement
            == "Checkout connection leak"
        )
        assert (
            result_a.supported_hypothesis.status
            == result_b.supported_hypothesis.status
            == HypothesisStatus.SUPPORTED
        )

        # Decision trace comparisons
        trace_a = result_a.state.decision_trace
        trace_b = result_b.state.decision_trace
        assert len(trace_a) == len(trace_b) == 3
        for step_a, step_b in zip(trace_a, trace_b, strict=True):
            assert step_a.action == step_b.action
            assert step_a.evaluation == step_b.evaluation
            assert step_a.hypothesis_status == step_b.hypothesis_status
