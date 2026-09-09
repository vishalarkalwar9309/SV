"""Deterministic tests for EvidenceManager and EvidenceStore provenance/auditability."""

from trace.engine.evidence_manager import (
    DuplicateEvidenceError,
    EvidenceManager,
    EvidenceNotFoundError,
    InvalidEvidenceClassificationError,
)
from trace.models.evidence import EvidenceItem, EvidenceStore
from trace.models.hypothesis import Hypothesis

import pytest


class TestEvidenceManager:
    def test_append_and_retrieve(self):
        em = EvidenceManager()
        item = EvidenceItem(
            id="ev-1",
            source_tool="query_metrics",
            query={"service": "checkout-db"},
            raw_data={"cpu_percent": 12.0},
        )
        returned = em.add(item)
        assert returned.id == "ev-1"
        assert em.get("ev-1") is not None
        assert em.get_or_raise("ev-1").raw_data["cpu_percent"] == 12.0

    def test_get_nonexistent_returns_none_or_raises(self):
        em = EvidenceManager()
        assert em.get("missing") is None
        with pytest.raises(EvidenceNotFoundError):
            em.get_or_raise("missing")

    def test_duplicate_id_prevented(self):
        em = EvidenceManager()
        item1 = EvidenceItem(id="ev-dup", source_tool="grep_logs", raw_data={})
        item2 = EvidenceItem(id="ev-dup", source_tool="query_metrics", raw_data={})
        em.add(item1)
        with pytest.raises(DuplicateEvidenceError):
            em.add(item2)

    def test_append_only_auditability(self):
        em = EvidenceManager()
        em.record_tool_result(
            "grep_logs", {"pattern": "error"}, True, {"count": 5}, evidence_id="e1"
        )
        em.record_tool_result(
            "grep_logs", {"pattern": "warn"}, True, {"count": 2}, evidence_id="e2"
        )
        all_items = em.list_all()
        assert len(all_items) == 2
        assert [i.id for i in all_items] == ["e1", "e2"]

    def test_provenance_preservation(self):
        em = EvidenceManager()
        em.record_tool_result(
            source_tool="fetch_recent_commits",
            query={"service": "checkout", "limit": 5},
            success=True,
            raw_data={"commits": [{"hash": "abc1234", "message": "fix: connection pool"}]},
            evidence_id="ev-prov",
        )
        retrieved = em.get_or_raise("ev-prov")
        assert retrieved.source_tool == "fetch_recent_commits"
        assert retrieved.query == {"service": "checkout", "limit": 5}
        assert retrieved.tool_succeeded is True
        assert retrieved.error_message is None
        assert len(retrieved.raw_data["commits"]) == 1

    def test_failed_tool_call_provenance(self):
        """Tool failure records availability provenance without throwing."""
        em = EvidenceManager()
        item = em.record_tool_result(
            source_tool="fetch_recent_commits",
            query={"service": "checkout"},
            success=False,
            raw_data=None,
            error_message="HTTP 503: Service Unavailable",
            evidence_id="ev-failed-tool",
        )
        assert item.tool_succeeded is False
        assert item.error_message == "HTTP 503: Service Unavailable"
        assert item.raw_data == {}

        failed = em.get_failed()
        assert len(failed) == 1
        assert failed[0].id == "ev-failed-tool"

        success = em.get_successful()
        assert len(success) == 0

    def test_failed_tool_cannot_be_root_cause_evidence(self):
        """Failed tool execution must be rejected from root-cause classification."""
        em = EvidenceManager()
        em.record_tool_result(
            source_tool="fetch_recent_commits",
            query={"service": "checkout"},
            success=False,
            error_message="HTTP 503: Service Unavailable",
            evidence_id="ev-fail-503",
        )
        with pytest.raises(InvalidEvidenceClassificationError) as exc_info:
            em.validate_root_cause_eligibility("ev-fail-503")
        assert "MUST NOT be classified" in str(exc_info.value)

    def test_successful_tool_is_root_cause_eligible(self):
        em = EvidenceManager()
        em.record_tool_result(
            source_tool="query_metrics",
            query={"metric": "cpu"},
            success=True,
            raw_data={"val": 10},
            evidence_id="ev-ok",
        )
        assert em.validate_root_cause_eligibility("ev-ok").id == "ev-ok"

    def test_hypothesis_linkage_retrieval(self):
        em = EvidenceManager()
        em.record_tool_result("query_metrics", {}, True, {"cpu": 12}, evidence_id="ev-cpu")
        em.record_tool_result("grep_logs", {}, True, {"pool": "exhausted"}, evidence_id="ev-pool")

        h = Hypothesis(
            statement="Database issue",
            supporting_evidence_ids=["ev-pool"],
            contradicting_evidence_ids=["ev-cpu"],
        )

        linked = em.get_for_hypothesis(h)
        assert len(linked["supporting"]) == 1
        assert linked["supporting"][0].id == "ev-pool"
        assert len(linked["contradicting"]) == 1
        assert linked["contradicting"][0].id == "ev-cpu"


class TestEvidenceStoreDirect:
    def test_duplicate_add_raises_value_error(self):
        store = EvidenceStore()
        item = EvidenceItem(id="dup", source_tool="test", raw_data={})
        store.add(item)
        with pytest.raises(ValueError, match="already exists"):
            store.add(item)

    def test_list_all_returns_copy_list(self):
        store = EvidenceStore()
        store.add(EvidenceItem(id="i1", source_tool="t1", raw_data={}))
        items = store.list_all()
        assert len(items) == 1
        items.append(EvidenceItem(id="i2", source_tool="t2", raw_data={}))
        assert len(store.list_all()) == 1  # store unmodified
