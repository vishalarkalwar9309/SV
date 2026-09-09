"""grep_logs — Search application logs by service and text pattern."""

from __future__ import annotations

from trace.models.action import ActionResult
from trace.simulator.models import ScenarioData
from trace.tools.base import BaseTool
from typing import Any

from pydantic import BaseModel, Field, ValidationError


class GrepLogsParams(BaseModel):
    """Typed input parameters for the grep_logs tool."""

    service: str = Field(description="Service name to search logs for")
    pattern: str = Field(
        description="Text pattern to search for (case-insensitive substring match)"
    )
    level: str | None = Field(
        default=None,
        description="Optional: filter by log level (INFO, WARN, ERROR)",
    )


class GrepLogsTool(BaseTool):
    """Search application logs for a service by text pattern.

    Queries the simulator's seeded log data. Returns matching log records.
    Never fabricates log entries.
    """

    def __init__(self, scenario: ScenarioData) -> None:
        self._scenario = scenario

    @property
    def name(self) -> str:
        return "grep_logs"

    @property
    def description(self) -> str:
        return (
            "Search application logs for a specific service using a text pattern. "
            "Returns matching log entries with timestamps, levels, and messages."
        )

    def get_parameters_schema(self) -> dict[str, Any]:
        return GrepLogsParams.model_json_schema()

    def execute(self, **params: Any) -> ActionResult:
        # Validate input
        try:
            parsed = GrepLogsParams(**params)
        except ValidationError as e:
            return ActionResult(action_id="", success=False, error=f"Invalid parameters: {e}")

        # Check for simulated failure
        failure = self._scenario.tool_failures.get(self.name)
        if failure and failure.enabled:
            return ActionResult(action_id="", success=False, error=failure.error_message)

        # Search logs deterministically
        matches = []
        for log in self._scenario.logs:
            if log.service != parsed.service:
                continue
            if parsed.pattern and parsed.pattern.lower() not in log.message.lower():
                continue
            if parsed.level is not None and log.level != parsed.level.upper():
                continue
            matches.append(log.model_dump())

        return ActionResult(
            action_id="",
            success=True,
            data={"matches": matches, "count": len(matches)},
        )
