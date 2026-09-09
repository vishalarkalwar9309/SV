"""Evidence manager — deterministic management, provenance, and auditability for evidence."""

from __future__ import annotations

from trace.models.evidence import EvidenceItem, EvidenceStore
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trace.models.hypothesis import Hypothesis


class EvidenceError(Exception):
    """Base error for evidence operations."""


class DuplicateEvidenceError(EvidenceError, ValueError):
    """Raised when an evidence item with the same ID is added."""


class EvidenceNotFoundError(EvidenceError, KeyError):
    """Raised when an evidence item is not found."""


class InvalidEvidenceClassificationError(EvidenceError, ValueError):
    """Raised when invalid evidence (e.g. failed tool call) is classified as root-cause evidence."""


class EvidenceManager:
    """Deterministic evidence manager handling auditability, provenance, and hypothesis linkage.

    Guarantees:
    - Append-only audit trail
    - No silent overwrites
    - Full provenance retention (tool, query, raw data, timestamp, success/error)
    - Preservation of failed tool calls as tool availability provenance
    - Prevention of failed tool calls being classified as supporting/contradicting root cause
    """

    def __init__(self, store: EvidenceStore | None = None) -> None:
        self.store = store if store is not None else EvidenceStore()

    def add(self, item: EvidenceItem) -> EvidenceItem:
        """Append an evidence item to the store, preserving provenance."""
        try:
            self.store.add(item)
        except ValueError as err:
            raise DuplicateEvidenceError(str(err)) from err
        return item

    def record_tool_result(
        self,
        source_tool: str,
        query: dict,
        success: bool,
        raw_data: dict | None = None,
        error_message: str | None = None,
        evidence_id: str | None = None,
    ) -> EvidenceItem:
        """Record a tool execution result as deterministic evidence with full provenance."""
        kwargs: dict = {
            "source_tool": source_tool,
            "query": query,
            "raw_data": raw_data if raw_data is not None else {},
            "tool_succeeded": success,
            "error_message": error_message,
        }
        if evidence_id is not None:
            kwargs["id"] = evidence_id

        item = EvidenceItem(**kwargs)
        return self.add(item)

    def get(self, evidence_id: str) -> EvidenceItem | None:
        """Retrieve evidence item by ID."""
        return self.store.get(evidence_id)

    def get_or_raise(self, evidence_id: str) -> EvidenceItem:
        """Retrieve evidence item by ID or raise EvidenceNotFoundError."""
        item = self.get(evidence_id)
        if item is None:
            raise EvidenceNotFoundError(f"Evidence with ID '{evidence_id}' not found")
        return item

    def list_all(self) -> list[EvidenceItem]:
        """Return all evidence items in append order."""
        return self.store.list_all()

    def get_successful(self) -> list[EvidenceItem]:
        """Return only evidence items from successful tool executions."""
        return self.store.get_successful()

    def get_failed(self) -> list[EvidenceItem]:
        """Return evidence items from failed tool calls (tool availability provenance)."""
        return self.store.get_failed()

    def get_for_hypothesis(self, hypothesis: Hypothesis) -> dict[str, list[EvidenceItem]]:
        """Retrieve evidence items associated with a hypothesis, grouped by relation."""
        return {
            "supporting": self.store.get_by_ids(hypothesis.supporting_evidence_ids),
            "contradicting": self.store.get_by_ids(hypothesis.contradicting_evidence_ids),
        }

    def validate_root_cause_eligibility(self, evidence_id: str) -> EvidenceItem:
        """Validate that the evidence is eligible to support or contradict a root-cause hypothesis.

        A failed tool call represents tool availability/health issues, NOT proof of
        an infrastructure/application root cause. Attempting to classify a tool failure
        as supporting or contradicting evidence raises InvalidEvidenceClassificationError.
        """
        item = self.get_or_raise(evidence_id)
        if not item.tool_succeeded:
            raise InvalidEvidenceClassificationError(
                f"Evidence '{evidence_id}' represents a failed tool execution "
                f"({item.source_tool}: {item.error_message}). "
                "Tool failures indicate tool unavailability and MUST NOT be classified "
                "as supporting or contradicting root-cause hypotheses."
            )
        return item
