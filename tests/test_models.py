"""Tests for TRACE 2.0 Pydantic domain models.

These tests verify model instantiation, defaults, validation,
and key behaviors — all without any LLM dependency.
"""

from datetime import UTC, datetime
from trace.models.action import ActionResult, AgentAction, RemediationRecommendation
from trace.models.evidence import EvidenceItem, EvidenceStore
from trace.models.hypothesis import Hypothesis, HypothesisStatus
from trace.models.incident import Incident, Severity

# ---------------------------------------------------------------------------
# Incident
# ---------------------------------------------------------------------------

class TestIncident:
    def test_create_minimal(self):
        inc = Incident(
            title="Checkout 502s",
            service="checkout",
            severity=Severity.HIGH,
            initial_observation="Database connection timeout errors spiking",
        )
        assert inc.title == "Checkout 502s"
        assert inc.service == "checkout"
        assert inc.severity == Severity.HIGH
        assert inc.id  # auto-generated
        assert isinstance(inc.timestamp, datetime)

    def test_ground_truth_defaults_none(self):
        inc = Incident(
            title="Test",
            service="svc",
            severity=Severity.LOW,
            initial_observation="Something",
        )
        assert inc.ground_truth_root_cause is None

    def test_ground_truth_set_by_simulator(self):
        inc = Incident(
            title="Test",
            service="checkout",
            severity=Severity.CRITICAL,
            initial_observation="502 errors",
            ground_truth_root_cause="Connection leak in checkout deploy v1.2.3",
        )
        assert inc.ground_truth_root_cause == "Connection leak in checkout deploy v1.2.3"

    def test_severity_enum_values(self):
        assert Severity.CRITICAL == "critical"
        assert Severity.HIGH == "high"
        assert Severity.MEDIUM == "medium"
        assert Severity.LOW == "low"


# ---------------------------------------------------------------------------
# Hypothesis
# ---------------------------------------------------------------------------

class TestHypothesis:
    def test_create_defaults(self):
        h = Hypothesis(statement="Database overload")
        assert h.status == HypothesisStatus.PROPOSED
        assert h.supporting_evidence_ids == []
        assert h.contradicting_evidence_ids == []
        assert h.disproval_reason is None
        assert h.id  # auto-generated

    def test_is_terminal_proposed(self):
        h = Hypothesis(statement="Test")
        assert h.is_terminal() is False

    def test_is_terminal_investigating(self):
        h = Hypothesis(statement="Test", status=HypothesisStatus.INVESTIGATING)
        assert h.is_terminal() is False

    def test_is_terminal_insufficient(self):
        h = Hypothesis(statement="Test", status=HypothesisStatus.INSUFFICIENT)
        assert h.is_terminal() is False

    def test_is_terminal_supported(self):
        h = Hypothesis(statement="Test", status=HypothesisStatus.SUPPORTED)
        assert h.is_terminal() is True

    def test_is_terminal_disproven(self):
        h = Hypothesis(
            statement="Test",
            status=HypothesisStatus.DISPROVEN,
            disproval_reason="Metrics show normal CPU",
        )
        assert h.is_terminal() is True
        assert h.disproval_reason == "Metrics show normal CPU"

    def test_status_enum_values(self):
        assert HypothesisStatus.PROPOSED == "proposed"
        assert HypothesisStatus.DISPROVEN == "disproven"
        assert HypothesisStatus.SUPPORTED == "supported"


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class TestEvidenceItem:
    def test_create_successful(self):
        e = EvidenceItem(
            source_tool="query_metrics",
            query={"service": "checkout-db", "metric": "cpu_percent"},
            raw_data={"cpu_percent": 12.0, "memory_percent": 45.0},
        )
        assert e.tool_succeeded is True
        assert e.error_message is None
        assert e.raw_data["cpu_percent"] == 12.0

    def test_create_failed(self):
        e = EvidenceItem(
            source_tool="fetch_recent_commits",
            query={"service": "checkout"},
            raw_data={},
            tool_succeeded=False,
            error_message="HTTP 503: Service Unavailable",
        )
        assert e.tool_succeeded is False
        assert "503" in e.error_message


class TestEvidenceStore:
    def _make_store(self) -> EvidenceStore:
        store = EvidenceStore()
        store.add(EvidenceItem(
            id="ev-metrics-1",
            source_tool="query_metrics",
            query={"service": "db"},
            raw_data={"cpu": 12},
        ))
        store.add(EvidenceItem(
            id="ev-logs-1",
            source_tool="grep_logs",
            query={"pattern": "timeout"},
            raw_data={"lines": ["connection timeout at 14:32"]},
        ))
        store.add(EvidenceItem(
            id="ev-commits-fail",
            source_tool="fetch_recent_commits",
            query={"service": "checkout"},
            raw_data={},
            tool_succeeded=False,
            error_message="HTTP 503",
        ))
        return store

    def test_add_and_get(self):
        store = self._make_store()
        assert len(store.items) == 3
        assert store.get("ev-metrics-1") is not None
        assert store.get("nonexistent") is None

    def test_get_by_tool(self):
        store = self._make_store()
        metrics = store.get_by_tool("query_metrics")
        assert len(metrics) == 1
        assert metrics[0].id == "ev-metrics-1"

    def test_get_successful(self):
        store = self._make_store()
        success = store.get_successful()
        assert len(success) == 2

    def test_get_failed(self):
        store = self._make_store()
        failed = store.get_failed()
        assert len(failed) == 1
        assert failed[0].id == "ev-commits-fail"

    def test_append_only(self):
        """Evidence store is append-only — items list grows monotonically."""
        store = EvidenceStore()
        assert len(store.items) == 0
        store.add(EvidenceItem(
            source_tool="grep_logs",
            query={},
            raw_data={"line": "test"},
        ))
        assert len(store.items) == 1
        store.add(EvidenceItem(
            source_tool="grep_logs",
            query={},
            raw_data={"line": "test2"},
        ))
        assert len(store.items) == 2


# ---------------------------------------------------------------------------
# Action
# ---------------------------------------------------------------------------

class TestAgentAction:
    def test_create(self):
        action = AgentAction(
            tool_name="query_metrics",
            params={"service": "checkout-db", "metric": "cpu_percent"},
            purpose="Check if database is overloaded to test H1",
            hypothesis_id="h-001",
        )
        assert action.tool_name == "query_metrics"
        assert action.hypothesis_id == "h-001"
        assert "overloaded" in action.purpose

    def test_no_hypothesis(self):
        action = AgentAction(
            tool_name="grep_logs",
            params={"pattern": "error"},
            purpose="Initial log scan",
        )
        assert action.hypothesis_id is None


class TestActionResult:
    def test_success(self):
        result = ActionResult(
            action_id="act-001",
            success=True,
            data={"cpu_percent": 12.0},
            evidence_id="ev-001",
        )
        assert result.success is True
        assert result.error is None

    def test_failure(self):
        result = ActionResult(
            action_id="act-002",
            success=False,
            error="HTTP 503: Service Unavailable",
        )
        assert result.success is False
        assert result.data is None


class TestRemediationRecommendation:
    def test_defaults_not_approved(self):
        rec = RemediationRecommendation(
            description="Rollback checkout service to v1.2.2",
            action_type="rollback",
            target_service="checkout",
            root_cause_hypothesis_id="h-002",
            confidence="high",
        )
        assert rec.approved is False
        assert rec.approved_by is None
        assert rec.approved_at is None

    def test_approval(self):
        rec = RemediationRecommendation(
            description="Rollback checkout service to v1.2.2",
            action_type="rollback",
            target_service="checkout",
            root_cause_hypothesis_id="h-002",
            confidence="high",
            approved=True,
            approved_by="oncall-engineer",
            approved_at=datetime.now(UTC),
        )
        assert rec.approved is True
        assert rec.approved_by == "oncall-engineer"
