"""LLM-backed planner implementation for TRACE 2.0.

Provides an LLM planner implementing BasePlanner.
The LLM proposes structured PlannedAction decisions.
It does NOT execute tools, fabricate observations, or evaluate evidence.
"""

from __future__ import annotations

import json
import os
from trace.agent.planner import BasePlanner, PlannedAction
from trace.engine.state import InvestigationState
from typing import Any

from pydantic import BaseModel


class PlannerError(Exception):
    """Raised when the LLM planner fails or produces invalid action data."""


class PlannerContext(BaseModel):
    """Concise context payload provided to the LLM planner.

    Explicitly excludes hidden simulator ground-truth metadata.
    """

    incident: dict[str, Any]
    hypotheses: list[dict[str, Any]]
    evidence_summary: list[dict[str, Any]]
    recent_actions: list[dict[str, Any]]
    available_tools: list[dict[str, Any]]
    investigation_status: str


DEFAULT_SYSTEM_INSTRUCTION = """You are the technical investigation planner for TRACE 2.0.
Your sole responsibility is to decide the next concrete investigation action
to test a candidate hypothesis.

Rules:
1. Propose EXACTLY ONE next action by selecting an available tool from the provided tool list.
2. Provide valid typed parameters adhering to the selected tool's schema.
3. State a concise, deterministic purpose explaining why this action was chosen.
4. Target a specific hypothesis ID that is currently active (PROPOSED, INVESTIGATING, INSUFFICIENT).
5. DO NOT investigate a hypothesis that is DISPROVEN or SUPPORTED.
6. Never invent logs, metrics, commits, or observations.
7. Do not claim a root cause or declare hypothesis status — evaluation is deterministic.
8. Do not treat tool failures as infrastructure evidence.
9. Do not include internal chain-of-thought reasoning; output only the structured action.
10. If no further action is needed or all hypotheses are resolved/disproven, return null/empty.
"""


def build_planner_context(
    state: InvestigationState,
    available_tools: list[dict[str, Any]],
) -> PlannerContext:
    """Build a concise, sanitized context payload for the planner.

    Guarantees:
    - Never exposes ground_truth_root_cause or simulator validation internals.
    - Clearly demarcates DISPROVEN and SUPPORTED hypotheses.
    - Distinguishes failed tool calls as availability errors, not root-cause evidence.
    """
    incident_info = {
        "id": state.incident.id,
        "title": state.incident.title,
        "service": state.incident.service,
        "severity": state.incident.severity.value,
        "initial_observation": state.incident.initial_observation,
        "timestamp": state.incident.timestamp.isoformat(),
    }

    hypotheses_info = []
    for h in state.hypotheses:
        hypotheses_info.append({
            "id": h.id,
            "statement": h.statement,
            "status": h.status.value,
            "is_terminal": h.is_terminal(),
            "disproval_reason": h.disproval_reason,
            "supporting_evidence_count": len(h.supporting_evidence_ids),
            "contradicting_evidence_count": len(h.contradicting_evidence_ids),
        })

    evidence_summary = []
    for ev in state.evidence:
        summary_item: dict[str, Any] = {
            "id": ev.id,
            "source_tool": ev.source_tool,
            "tool_succeeded": ev.tool_succeeded,
        }
        if not ev.tool_succeeded:
            summary_item["status"] = "TOOL_UNAVAILABLE"
            summary_item["error_message"] = ev.error_message
        else:
            summary_item["status"] = "SUCCESS"
            # Extract concise signals rather than dumping raw logs/metrics
            raw = ev.raw_data or {}
            if "metrics" in raw:
                summary_item["metrics_summary"] = {
                    k: v
                    for k, v in raw["metrics"].items()
                    if k in ("cpu_percent", "memory_percent", "active_db_connections")
                }
            elif "matches" in raw:
                summary_item["log_matches_count"] = len(raw["matches"])
            elif "commits" in raw:
                summary_item["commits_count"] = len(raw["commits"])
                if raw["commits"]:
                    summary_item["top_commit"] = {
                        "hash": raw["commits"][0].get("hash"),
                        "message": raw["commits"][0].get("message"),
                    }
        evidence_summary.append(summary_item)

    recent_actions = [
        {
            "tool_name": a.tool_name,
            "params": a.params,
            "purpose": a.purpose,
            "hypothesis_id": a.hypothesis_id,
        }
        for a in state.actions_taken[-5:]
    ]

    return PlannerContext(
        incident=incident_info,
        hypotheses=hypotheses_info,
        evidence_summary=evidence_summary,
        recent_actions=recent_actions,
        available_tools=available_tools,
        investigation_status=state.investigation_status.value,
    )


class LLMPlanner(BasePlanner):
    """Real LLM-backed investigation planner using Google Gemini API.

    Implements BasePlanner protocol. Produces structured PlannedAction instances.
    Accepts an injectable client for zero-network testing.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str = "gemini-2.5-flash",
        client: Any = None,
        system_instruction: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.system_instruction = system_instruction or DEFAULT_SYSTEM_INSTRUCTION

        if client is not None:
            self._client = client
        else:
            key = api_key or os.environ.get("GOOGLE_API_KEY")
            if key:
                from google import genai
                self._client = genai.Client(api_key=key)
            else:
                self._client = None

    def plan_next_action(
        self,
        state: InvestigationState,
        available_tools: list[dict[str, Any]],
    ) -> PlannedAction | None:
        """Construct context, prompt LLM, and parse strictly into PlannedAction."""
        if self._client is None:
            raise PlannerError(
                "Google GenAI client is not configured. "
                "Set GOOGLE_API_KEY environment variable or pass an injectable client."
            )

        context = build_planner_context(state, available_tools)
        prompt = json.dumps(context.model_dump(), indent=2)

        try:
            from google.genai import types

            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=PlannedAction,
                    system_instruction=self.system_instruction,
                ),
            )
        except Exception as e:
            raise PlannerError(f"LLM generation failed: {e}") from e

        return self._parse_response(response)

    def _parse_response(self, response: Any) -> PlannedAction | None:
        """Strictly parse and validate the LLM response into PlannedAction."""
        # 1. Direct parsed object if supported by SDK
        if getattr(response, "parsed", None) is not None:
            parsed = response.parsed
            if isinstance(parsed, PlannedAction):
                return parsed
            if isinstance(parsed, dict):
                return self._validate_dict(parsed)

        # 2. Text response JSON parsing
        text = getattr(response, "text", None)
        if not text or not text.strip() or text.strip() == "null":
            return None

        try:
            data = json.loads(text.strip())
        except Exception as e:
            raise PlannerError(f"LLM did not return valid JSON: {e}") from e

        if data is None or data == {}:
            return None

        return self._validate_dict(data)

    def _validate_dict(self, data: dict[str, Any]) -> PlannedAction:
        """Enforce strict PlannedAction schema without silent repairs."""
        try:
            action = PlannedAction.model_validate(data)
        except Exception as e:
            raise PlannerError(
                f"LLM structured response failed PlannedAction validation: {e}"
            ) from e

        if not action.tool_name or not action.tool_name.strip():
            raise PlannerError("PlannedAction tool_name cannot be empty")
        if not action.purpose or not action.purpose.strip():
            raise PlannerError("PlannedAction purpose cannot be empty")

        return action
