"""Deterministic tests for InvestigationState."""

from trace.engine.state import InvestigationState, InvestigationStatus
from trace.models.action import AgentAction
from trace.models.evidence import EvidenceItem
from trace.models.hypothesis import Hypothesis, HypothesisStatus
from trace.models.incident import Incident, Severity


def _make_incident() -> Incident:
    return Incident(
        id="inc-test-01",
        title="Checkout 502s",
        service="checkout",
        severity=Severity.HIGH,
        initial_observation="502 Bad Gateway spike",
    )


class TestInvestigationState:
    def test_state_coexistence(self):
        """Incident, multiple hypotheses, evidence, and actions coexist cleanly."""
        inc = _make_incident()
        h1 = Hypothesis(id="h1", statement="Connection leak", status=HypothesisStatus.INVESTIGATING)
        h2 = Hypothesis(id="h2", statement="High CPU", status=HypothesisStatus.DISPROVEN)

        ev1 = EvidenceItem(id="ev1", source_tool="query_metrics", raw_data={"cpu": 12})
        h1.supporting_evidence_ids.append("ev1")

        action = AgentAction(
            tool_name="query_metrics",
            params={"service": "checkout-db"},
            purpose="Investigate H1",
            hypothesis_id="h1",
        )

        state = InvestigationState(
            incident=inc,
            hypotheses=[h1, h2],
            evidence=[ev1],
            actions_taken=[action],
            current_hypothesis_id="h1",
            investigation_status=InvestigationStatus.ACTIVE,
        )

        assert state.incident.id == "inc-test-01"
        assert len(state.hypotheses) == 2
        assert len(state.evidence) == 1
        assert len(state.actions_taken) == 1
        assert state.investigation_status == InvestigationStatus.ACTIVE
        assert state.current_hypothesis is not None
        assert state.current_hypothesis.id == "h1"

    def test_evidence_references_remain_correct(self):
        inc = _make_incident()
        ev = EvidenceItem(id="ev-db", source_tool="grep_logs", raw_data={"line": "pool timeout"})
        h = Hypothesis(id="h-pool", statement="Pool timeout", supporting_evidence_ids=["ev-db"])

        state = InvestigationState(
            incident=inc,
            hypotheses=[h],
            evidence=[ev],
            current_hypothesis_id="h-pool",
        )

        # Look up hypothesis and resolve evidence
        hyp = state.get_hypothesis("h-pool")
        assert hyp is not None
        ref_id = hyp.supporting_evidence_ids[0]
        ev_resolved = state.get_evidence(ref_id)
        assert ev_resolved is not None
        assert ev_resolved.id == "ev-db"
        assert ev_resolved.raw_data["line"] == "pool timeout"

    def test_independent_instances_do_not_leak_mutable_state(self):
        """Verify two state instances have completely independent mutable collections."""
        state1 = InvestigationState(incident=_make_incident())
        state2 = InvestigationState(incident=_make_incident())

        # Modify state1 collections
        h = Hypothesis(id="h-unique", statement="Leak test")
        ev = EvidenceItem(id="ev-unique", source_tool="grep_logs", raw_data={})
        act = AgentAction(tool_name="grep_logs", purpose="Testing isolation")

        state1.hypotheses.append(h)
        state1.evidence.append(ev)
        state1.actions_taken.append(act)

        # Verify state2 remains untouched
        assert len(state2.hypotheses) == 0
        assert len(state2.evidence) == 0
        assert len(state2.actions_taken) == 0

    def test_serialization(self):
        """InvestigationState serializes to dict/json cleanly."""
        state = InvestigationState(
            incident=_make_incident(),
            hypotheses=[Hypothesis(id="h1", statement="Test hyp")],
            investigation_status=InvestigationStatus.ACTIVE,
        )
        data = state.model_dump()
        assert data["incident"]["id"] == "inc-test-01"
        assert len(data["hypotheses"]) == 1
        assert data["investigation_status"] == "active"
