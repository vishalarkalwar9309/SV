"""fetch_recent_commits — Retrieve recent deployments / commits for a service."""

from __future__ import annotations

from trace.models.action import ActionResult
from trace.simulator.models import ScenarioData
from trace.tools.base import BaseTool
from typing import Any

from pydantic import BaseModel, Field, ValidationError


class FetchRecentCommitsParams(BaseModel):
    """Typed input parameters for the fetch_recent_commits tool."""

    service: str = Field(description="Service name to retrieve commits for")
    limit: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum number of recent commits to return",
    )


class FetchRecentCommitsTool(BaseTool):
    """Retrieve recent deployment and commit information for a service.

    Queries the simulator's seeded commit/deployment data. Returns commit
    records with hashes, authors, messages, changed files, and diff summaries.
    Never fabricates commits.
    """

    def __init__(self, scenario: ScenarioData) -> None:
        self._scenario = scenario

    @property
    def name(self) -> str:
        return "fetch_recent_commits"

    @property
    def description(self) -> str:
        return (
            "Fetch recent commits and deployments for a service. "
            "Returns commit hashes, authors, messages, changed files, "
            "and diff summaries."
        )

    def get_parameters_schema(self) -> dict[str, Any]:
        return FetchRecentCommitsParams.model_json_schema()

    def execute(self, **params: Any) -> ActionResult:
        # Validate input
        try:
            parsed = FetchRecentCommitsParams(**params)
        except ValidationError as e:
            return ActionResult(action_id="", success=False, error=f"Invalid parameters: {e}")

        # Check for simulated failure
        failure = self._scenario.tool_failures.get(self.name)
        if failure and failure.enabled:
            return ActionResult(action_id="", success=False, error=failure.error_message)

        # Filter commits for the requested service, apply limit
        service_commits = [
            c.model_dump() for c in self._scenario.commits if c.service == parsed.service
        ]
        limited = service_commits[: parsed.limit]

        return ActionResult(
            action_id="",
            success=True,
            data={"commits": limited, "count": len(limited), "service": parsed.service},
        )
