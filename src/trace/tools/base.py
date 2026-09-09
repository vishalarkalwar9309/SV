"""BaseTool — minimal abstraction for deterministic investigation tools.

Design goals:
  - Expose name, description, and input schema for future agent discovery
  - Enforce deterministic, ActionResult-compatible output
  - Stay intentionally small — no registry, planner, or orchestration
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from trace.models.action import ActionResult
from typing import Any


class BaseTool(ABC):
    """Base class for all TRACE investigation tools.

    Subclasses must implement four members:

      name               — stable identifier (e.g. "grep_logs")
      description         — human-readable purpose summary
      get_parameters_schema() — JSON Schema for input params
      execute(**params)   — deterministic execution returning ActionResult

    ActionResult.action_id is set to "" by tools. The controller layer
    (not yet implemented) will fill it in when wiring actions to evidence.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable tool identifier used for selection and logging."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this tool investigates."""

    @abstractmethod
    def get_parameters_schema(self) -> dict[str, Any]:
        """JSON Schema describing the tool's input parameters.

        Enables a future planner/agent to discover what to pass.
        Typically generated via ``SomeParamsModel.model_json_schema()``.
        """

    @abstractmethod
    def execute(self, **params: Any) -> ActionResult:
        """Execute the tool with the given keyword parameters.

        Contract:
          - Must be deterministic (identical inputs → identical outputs).
          - Must never fabricate infrastructure data.
          - Returns ``ActionResult(action_id="", ...)`` — the controller
            assigns the real action_id later.
        """
