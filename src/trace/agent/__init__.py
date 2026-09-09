"""TRACE Agent — orchestration boundary between planning and deterministic execution."""

from __future__ import annotations

from trace.agent.controller import (
    ActionValidationError,
    AgentController,
    ControllerError,
    ControllerStepResult,
    StepLimitExceededError,
)
from trace.agent.investigation_loop import AutonomousInvestigationLoop
from trace.agent.llm_planner import (
    DEFAULT_GEMINI_MODEL,
    LLMPlanner,
    PlannerContext,
    PlannerError,
    build_planner_context,
)
from trace.agent.planner import (
    BasePlanner,
    MockPlanner,
    PlannedAction,
)
from trace.agent.registry import (
    DuplicateToolError,
    RegistryError,
    ToolNotFoundError,
    ToolRegistry,
)

__all__ = [
    "ActionValidationError",
    "AgentController",
    "AutonomousInvestigationLoop",
    "BasePlanner",
    "ControllerError",
    "ControllerStepResult",
    "DEFAULT_GEMINI_MODEL",
    "DuplicateToolError",
    "LLMPlanner",
    "MockPlanner",
    "PlannedAction",
    "PlannerContext",
    "PlannerError",
    "RegistryError",
    "StepLimitExceededError",
    "ToolNotFoundError",
    "ToolRegistry",
    "build_planner_context",
]
