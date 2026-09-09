"""query_metrics — Retrieve infrastructure metrics for a service."""

from __future__ import annotations

from trace.models.action import ActionResult
from trace.simulator.models import ScenarioData
from trace.tools.base import BaseTool
from typing import Any

from pydantic import BaseModel, Field, ValidationError


class QueryMetricsParams(BaseModel):
    """Typed input parameters for the query_metrics tool."""

    service: str = Field(description="Service or resource name to query metrics for")
    metric_name: str | None = Field(
        default=None,
        description="Optional: specific metric name to retrieve (returns all if omitted)",
    )


class QueryMetricsTool(BaseTool):
    """Retrieve infrastructure metrics for a service or resource.

    Queries the simulator's seeded metric data. Returns metric snapshots.
    Never fabricates metric values.
    """

    def __init__(self, scenario: ScenarioData) -> None:
        self._scenario = scenario

    @property
    def name(self) -> str:
        return "query_metrics"

    @property
    def description(self) -> str:
        return (
            "Query infrastructure metrics for a specific service or resource. "
            "Returns metric values such as CPU, memory, connection counts, and latency."
        )

    def get_parameters_schema(self) -> dict[str, Any]:
        return QueryMetricsParams.model_json_schema()

    def execute(self, **params: Any) -> ActionResult:
        # Validate input
        try:
            parsed = QueryMetricsParams(**params)
        except ValidationError as e:
            return ActionResult(action_id="", success=False, error=f"Invalid parameters: {e}")

        # Check for simulated failure
        failure = self._scenario.tool_failures.get(self.name)
        if failure and failure.enabled:
            return ActionResult(action_id="", success=False, error=failure.error_message)

        # Find metrics for the requested service
        service_metrics = None
        for sm in self._scenario.metrics:
            if sm.service == parsed.service:
                service_metrics = sm
                break

        if service_metrics is None:
            return ActionResult(
                action_id="",
                success=True,
                data={"service": parsed.service, "found": False, "metrics": {}},
            )

        # Optionally filter to a specific metric
        if parsed.metric_name is not None:
            value = service_metrics.metrics.get(parsed.metric_name)
            return ActionResult(
                action_id="",
                success=True,
                data={
                    "service": parsed.service,
                    "found": True,
                    "metric_name": parsed.metric_name,
                    "metric_found": value is not None,
                    "metrics": {parsed.metric_name: value} if value is not None else {},
                },
            )

        # Return all metrics for the service
        return ActionResult(
            action_id="",
            success=True,
            data={
                "service": parsed.service,
                "found": True,
                "metrics": dict(service_metrics.metrics),
            },
        )
