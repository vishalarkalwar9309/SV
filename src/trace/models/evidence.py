"""Evidence model — the ground truth collected by tools."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    """A single piece of evidence returned by an investigation tool.

    Evidence is ALWAYS produced by a deterministic tool, never by the LLM.
    The raw_data field contains the actual tool output — logs, metrics, commits, etc.
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:10])
    source_tool: str
    query: dict = Field(default_factory=dict, description="The parameters passed to the tool")
    raw_data: dict = Field(description="The raw result from the tool")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tool_succeeded: bool = True
    error_message: str | None = None


class EvidenceStore(BaseModel):
    """Append-only store of all evidence collected during an investigation.

    Evidence is never mutated or deleted — only appended.
    This gives us a complete audit trail.
    """

    items: list[EvidenceItem] = Field(default_factory=list)

    def add(self, item: EvidenceItem) -> None:
        """Append a new evidence item."""
        self.items.append(item)

    def get(self, evidence_id: str) -> EvidenceItem | None:
        """Retrieve an evidence item by ID."""
        for item in self.items:
            if item.id == evidence_id:
                return item
        return None

    def get_by_tool(self, tool_name: str) -> list[EvidenceItem]:
        """Retrieve all evidence from a specific tool."""
        return [item for item in self.items if item.source_tool == tool_name]

    def get_successful(self) -> list[EvidenceItem]:
        """Retrieve only evidence from successful tool calls."""
        return [item for item in self.items if item.tool_succeeded]

    def get_failed(self) -> list[EvidenceItem]:
        """Retrieve evidence entries from failed tool calls."""
        return [item for item in self.items if not item.tool_succeeded]
