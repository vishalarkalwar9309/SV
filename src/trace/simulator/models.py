"""Simulator data models for TRACE 2.0.

These models represent seeded infrastructure data that investigation tools query.
They are internal to the simulator — ground truth is never exposed to the agent.
"""

from __future__ import annotations

from trace.models.incident import Incident

from pydantic import BaseModel, Field


class LogRecord(BaseModel):
    """A single log entry from a service."""

    timestamp: str
    level: str  # INFO, WARN, ERROR, DEBUG
    service: str
    message: str


class ServiceMetrics(BaseModel):
    """A metrics snapshot for a service or resource."""

    service: str
    metrics: dict[str, float | int | str] = Field(default_factory=dict)


class CommitRecord(BaseModel):
    """A deployment / commit record for a service."""

    hash: str
    author: str
    message: str
    timestamp: str
    service: str
    files_changed: list[str] = Field(default_factory=list)
    diff_summary: str


class ToolFailureConfig(BaseModel):
    """Configuration for simulating a deterministic tool failure."""

    enabled: bool = False
    error_message: str = "HTTP 503: Service Unavailable"


class GroundTruth(BaseModel):
    """Simulator-only validation metadata. NEVER exposed to the agent or tools."""

    root_cause: str
    root_cause_service: str
    root_cause_commit_hash: str | None = None
    disproven_hypotheses: list[str] = Field(default_factory=list)


class ScenarioData(BaseModel):
    """Complete seeded scenario: incident, infrastructure data, and ground truth."""

    scenario_id: str
    incident: Incident
    logs: list[LogRecord] = Field(default_factory=list)
    metrics: list[ServiceMetrics] = Field(default_factory=list)
    commits: list[CommitRecord] = Field(default_factory=list)
    ground_truth: GroundTruth
    tool_failures: dict[str, ToolFailureConfig] = Field(default_factory=dict)
