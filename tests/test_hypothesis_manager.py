"""Deterministic tests for HypothesisManager."""

from trace.engine.evidence_manager import InvalidEvidenceClassificationError
from trace.engine.hypothesis_manager import (
    DuplicateHypothesisError,
    HypothesisManager,
    HypothesisNotFoundError,
    InvalidTransitionError,
)
from trace.models.evidence import EvidenceItem, EvidenceStore
from trace.models.hypothesis import Hypothesis, HypothesisStatus

import pytest


class TestHypothesisManagerCRUD:
    def test_create_hypothesis(self):
        mgr = HypothesisManager()
        h = mgr.create(statement="Database pool exhaustion", hypothesis_id="hyp-01")
        assert h.id == "hyp-01"
        assert h.statement == "Database pool exhaustion"
        assert h.status == HypothesisStatus.PROPOSED
        assert h.supporting_evidence_ids == []
        assert h.contradicting_evidence_ids == []

    def test_create_generates_id_if_none(self):
        mgr = HypothesisManager()
        h = mgr.create(statement="Auto generated ID")
        assert h.id is not None
        assert len(h.id) > 0

    def test_register_hypothesis(self):
        mgr = HypothesisManager()
        h = Hypothesis(id="hyp-custom", statement="Custom object")
        registered = mgr.register(h)
        assert registered.id == "hyp-custom"
        assert mgr.get("hyp-custom") is h

    def test_register_duplicate_raises_error(self):
        mgr = HypothesisManager()
        mgr.create(statement="First", hypothesis_id="hyp-dup")
        with pytest.raises(DuplicateHypothesisError):
            mgr.create(statement="Duplicate", hypothesis_id="hyp-dup")

    def test_get_existing_and_nonexistent(self):
        mgr = HypothesisManager()
        mgr.create(statement="Exists", hypothesis_id="hyp-01")
        assert mgr.get("hyp-01") is not None
        assert mgr.get("hyp-missing") is None

    def test_get_or_raise(self):
        mgr = HypothesisManager()
        mgr.create(statement="Exists", hypothesis_id="hyp-01")
        assert mgr.get_or_raise("hyp-01").id == "hyp-01"
        with pytest.raises(HypothesisNotFoundError):
            mgr.get_or_raise("hyp-missing")

    def test_list_all_and_filtered(self):
        mgr = HypothesisManager()
        mgr.create(statement="H1", hypothesis_id="h1")
        mgr.create(statement="H2", hypothesis_id="h2")
        mgr.update_status("h2", HypothesisStatus.INVESTIGATING)

        all_hyp = mgr.list_all()
        assert len(all_hyp) == 2

        proposed = mgr.list_all(status=HypothesisStatus.PROPOSED)
        assert len(proposed) == 1
        assert proposed[0].id == "h1"

        investigating = mgr.list_all(status=HypothesisStatus.INVESTIGATING)
        assert len(investigating) == 1
        assert investigating[0].id == "h2"


class TestHypothesisTransitions:
    def test_valid_lifecycle_to_supported(self):
        """PROPOSED -> INVESTIGATING -> SUPPORTED (terminal)."""
        mgr = HypothesisManager()
        mgr.create(statement="Test", hypothesis_id="h1")

        mgr.update_status("h1", HypothesisStatus.INVESTIGATING)
        assert mgr.get_or_raise("h1").status == HypothesisStatus.INVESTIGATING

        mgr.update_status("h1", HypothesisStatus.SUPPORTED)
        assert mgr.get_or_raise("h1").status == HypothesisStatus.SUPPORTED
        assert mgr.get_or_raise("h1").is_terminal() is True

    def test_valid_lifecycle_to_disproven(self):
        """PROPOSED -> INVESTIGATING -> DISPROVEN (terminal with reason)."""
        mgr = HypothesisManager()
        mgr.create(statement="Test", hypothesis_id="h1")

        mgr.update_status("h1", HypothesisStatus.INVESTIGATING)
        mgr.update_status("h1", HypothesisStatus.DISPROVEN, disproval_reason="Metrics normal")

        h = mgr.get_or_raise("h1")
        assert h.status == HypothesisStatus.DISPROVEN
        assert h.disproval_reason == "Metrics normal"
        assert h.is_terminal() is True

    def test_valid_lifecycle_insufficient_and_reopen(self):
        """PROPOSED -> INVESTIGATING -> INSUFFICIENT -> INVESTIGATING."""
        mgr = HypothesisManager()
        mgr.create(statement="Test", hypothesis_id="h1")

        mgr.update_status("h1", HypothesisStatus.INVESTIGATING)
        mgr.update_status("h1", HypothesisStatus.INSUFFICIENT)
        assert mgr.get_or_raise("h1").status == HypothesisStatus.INSUFFICIENT
        assert mgr.get_or_raise("h1").is_terminal() is False

        # Can reopen for investigation
        mgr.update_status("h1", HypothesisStatus.INVESTIGATING)
        assert mgr.get_or_raise("h1").status == HypothesisStatus.INVESTIGATING

    def test_invalid_transition_from_proposed_directly_to_supported(self):
        mgr = HypothesisManager()
        mgr.create(statement="Test", hypothesis_id="h1")

        with pytest.raises(InvalidTransitionError):
            mgr.update_status("h1", HypothesisStatus.SUPPORTED)

    def test_invalid_transition_from_proposed_directly_to_disproven(self):
        mgr = HypothesisManager()
        mgr.create(statement="Test", hypothesis_id="h1")

        with pytest.raises(InvalidTransitionError):
            mgr.update_status("h1", HypothesisStatus.DISPROVEN)

    def test_terminal_supported_cannot_transition(self):
        mgr = HypothesisManager()
        mgr.create(statement="Test", hypothesis_id="h1")
        mgr.update_status("h1", HypothesisStatus.INVESTIGATING)
        mgr.update_status("h1", HypothesisStatus.SUPPORTED)

        for target in (
            HypothesisStatus.PROPOSED,
            HypothesisStatus.INVESTIGATING,
            HypothesisStatus.DISPROVEN,
            HypothesisStatus.INSUFFICIENT,
        ):
            with pytest.raises(InvalidTransitionError):
                mgr.update_status("h1", target)

    def test_terminal_disproven_cannot_transition(self):
        mgr = HypothesisManager()
        mgr.create(statement="Test", hypothesis_id="h1")
        mgr.update_status("h1", HypothesisStatus.INVESTIGATING)
        mgr.update_status("h1", HypothesisStatus.DISPROVEN)

        for target in (
            HypothesisStatus.PROPOSED,
            HypothesisStatus.INVESTIGATING,
            HypothesisStatus.SUPPORTED,
            HypothesisStatus.INSUFFICIENT,
        ):
            with pytest.raises(InvalidTransitionError):
                mgr.update_status("h1", target)


class TestEvidenceAttachment:
    def test_attach_supporting_evidence_ids(self):
        mgr = HypothesisManager()
        mgr.create(statement="H1", hypothesis_id="h1")
        mgr.attach_supporting_evidence("h1", "ev-001")
        mgr.attach_supporting_evidence("h1", "ev-002")
        # Idempotency: duplicate ID not appended twice
        mgr.attach_supporting_evidence("h1", "ev-001")

        h = mgr.get_or_raise("h1")
        assert h.supporting_evidence_ids == ["ev-001", "ev-002"]

    def test_attach_contradicting_evidence_ids(self):
        mgr = HypothesisManager()
        mgr.create(statement="H1", hypothesis_id="h1")
        mgr.attach_contradicting_evidence("h1", "ev-contra-01")
        mgr.attach_contradicting_evidence("h1", "ev-contra-01")

        h = mgr.get_or_raise("h1")
        assert h.contradicting_evidence_ids == ["ev-contra-01"]

    def test_reject_failed_tool_as_supporting_evidence(self):
        store = EvidenceStore()
        store.add(EvidenceItem(
            id="ev-fail-01",
            source_tool="fetch_recent_commits",
            query={"service": "checkout"},
            raw_data={},
            tool_succeeded=False,
            error_message="HTTP 503",
        ))

        mgr = HypothesisManager(evidence_store=store)
        mgr.create(statement="Database issue", hypothesis_id="h1")

        with pytest.raises(InvalidEvidenceClassificationError):
            mgr.attach_supporting_evidence("h1", "ev-fail-01")

    def test_reject_failed_tool_as_contradicting_evidence(self):
        store = EvidenceStore()
        store.add(EvidenceItem(
            id="ev-fail-01",
            source_tool="query_metrics",
            query={},
            raw_data={},
            tool_succeeded=False,
            error_message="Connection timeout",
        ))

        mgr = HypothesisManager(evidence_store=store)
        mgr.create(statement="Network issue", hypothesis_id="h1")

        with pytest.raises(InvalidEvidenceClassificationError):
            mgr.attach_contradicting_evidence("h1", "ev-fail-01")


class TestMultipleCompetingHypotheses:
    def test_competing_hypotheses_separation(self):
        """Simulate triage with 3 competing hypotheses: one supported, one disproven, one active."""
        mgr = HypothesisManager()
        mgr.create("Connection leak in recent deploy", hypothesis_id="h-deploy")
        mgr.create("Database CPU saturation", hypothesis_id="h-cpu")
        mgr.create("Network switch packet loss", hypothesis_id="h-net")

        # Start investigating h-cpu and h-deploy
        mgr.update_status("h-cpu", HypothesisStatus.INVESTIGATING)
        mgr.update_status("h-deploy", HypothesisStatus.INVESTIGATING)

        # Evidence shows CPU is normal (12%), disproving h-cpu
        mgr.update_status(
            "h-cpu",
            HypothesisStatus.DISPROVEN,
            disproval_reason="Metrics confirm cpu_percent < 20%",
        )

        # Evidence shows deploy introduced unclosed db session, supporting h-deploy
        mgr.update_status("h-deploy", HypothesisStatus.SUPPORTED)

        # Verify clear separation
        supported = mgr.get_supported()
        assert len(supported) == 1
        assert supported[0].id == "h-deploy"
        assert mgr.get_root_cause_candidates() == supported

        disproven = mgr.get_disproven()
        assert len(disproven) == 1
        assert disproven[0].id == "h-cpu"
        assert disproven[0].disproval_reason == "Metrics confirm cpu_percent < 20%"

        active = mgr.get_active()
        assert len(active) == 1
        assert active[0].id == "h-net"
        assert active[0].status == HypothesisStatus.PROPOSED
