"""Planner contract and minimal mock/deterministic planners for TRACE 2.0.

The planner proposes structured actions based on the current state.
It does NOT execute tools or evaluate evidence — those belong to the deterministic engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from trace.engine.state import InvestigationState
from trace.models.action import AgentAction
from typing import Any

from pydantic import BaseModel, Field


class PlannedAction(BaseModel):
    """A structured plan proposed by a planner.

    Must contain explicit, typed action parameters and purpose.
    Does NOT contain chain-of-thought reasoning.
    """

    tool_name: str = Field(description="Name of the registered tool to execute")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters passed to the tool",
    )
    purpose: str = Field(description="Deterministic summary of why this action was chosen")
    hypothesis_id: str | None = Field(
        default=None,
        description="Optional target hypothesis this action is testing",
    )

    def to_agent_action(self) -> AgentAction:
        """Convert this planned action to an AgentAction record."""
        return AgentAction(
            tool_name=self.tool_name,
            params=self.parameters,
            purpose=self.purpose,
            hypothesis_id=self.hypothesis_id,
        )


class BasePlanner(ABC):
    """Protocol / ABC for planners in TRACE 2.0.

    Planners can be deterministic (for testing/baselines) or LLM-driven.
    """

    @abstractmethod
    def plan_next_action(
        self,
        state: InvestigationState,
        available_tools: list[dict[str, Any]],
    ) -> PlannedAction | None:
        """Propose the next structured action, or None if the investigation is complete."""


class MockPlanner(BasePlanner):
    """Deterministic / scripted planner for testing the controller orchestration boundary.

    Consumes a pre-configured sequence of PlannedAction objects.
    """

    def __init__(self, actions: list[PlannedAction] | None = None) -> None:
        self._actions: list[PlannedAction] = list(actions or [])

    def add_action(self, action: PlannedAction) -> None:
        """Enqueue an action to be returned."""
        self._actions.append(action)

    def plan_next_action(
        self,
        state: InvestigationState,
        available_tools: list[dict[str, Any]],
    ) -> PlannedAction | None:
        """Return the next planned action from the queue, or None if empty."""
        if not self._actions:
            return None
        return self._actions.pop(0)
