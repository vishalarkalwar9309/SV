"""Tool registry — minimal explicit registry for TRACE investigation tools."""

from __future__ import annotations

from trace.tools.base import BaseTool
from typing import Any


class RegistryError(Exception):
    """Base error for tool registry operations."""


class ToolNotFoundError(RegistryError, KeyError):
    """Raised when an unregistered tool is requested."""


class DuplicateToolError(RegistryError, ValueError):
    """Raised when registering a tool under an existing name."""


class ToolRegistry:
    """Minimal, explicit registry mapping tool names to BaseTool implementations.

    Safety:
    - Only accepts instances of BaseTool
    - Disallows arbitrary Python execution or dynamic code evaluation
    - Fails safely on unknown tool names
    """

    def __init__(self, tools: list[BaseTool] | None = None) -> None:
        self._tools: dict[str, BaseTool] = {}
        if tools:
            for tool in tools:
                self.register(tool)

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance under its stable name."""
        if not isinstance(tool, BaseTool):
            raise TypeError(f"Expected BaseTool instance, got {type(tool).__name__}")
        if tool.name in self._tools:
            raise DuplicateToolError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """Lookup a tool by name, returning None if not registered."""
        return self._tools.get(name)

    def get_or_raise(self, name: str) -> BaseTool:
        """Lookup a tool by name or raise ToolNotFoundError."""
        tool = self.get(name)
        if tool is None:
            raise ToolNotFoundError(f"Tool '{name}' is not registered in ToolRegistry")
        return tool

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def list_tools(self) -> list[BaseTool]:
        """Return all registered tool instances."""
        return list(self._tools.values())

    def get_metadata(self) -> list[dict[str, Any]]:
        """Return metadata for all registered tools, suitable for planners."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters_schema": tool.get_parameters_schema(),
            }
            for tool in self._tools.values()
        ]
