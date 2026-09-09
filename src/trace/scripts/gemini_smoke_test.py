"""Opt-in real Gemini API smoke test for TRACE 2.0.

Executes a single live Gemini request to verify that LLMPlanner can connect,
prompt the Gemini API, and return a validated PlannedAction.

Usage:
    python -m trace.scripts.gemini_smoke_test
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Load .env if present
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def run_smoke_test() -> int:
    """Run a single real Gemini smoke test.

    Returns:
        0 on success or clean skip.
        1 on live API failure.
    """
    _src_dir = str(Path(__file__).resolve().parent.parent.parent)
    if _src_dir not in sys.path:
        sys.path.insert(0, _src_dir)

    from trace.agent.llm_planner import LLMPlanner
    from trace.agent.planner import PlannedAction
    from trace.agent.registry import ToolRegistry
    from trace.engine.state import InvestigationState, InvestigationStatus
    from trace.models.hypothesis import Hypothesis
    from trace.simulator.scenarios.checkout_502 import create as create_checkout_scenario
    from trace.tools.grep_logs import GrepLogsTool
    from trace.tools.query_metrics import QueryMetricsTool
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key or api_key in ("your-api-key-here", "your_api_key_here"):
        print("Real Gemini smoke test not executed because GOOGLE_API_KEY is not configured.")
        return 0

    model_name = (os.environ.get("GEMINI_MODEL") or "gemini-3.6-flash").strip()
    print(f"[SMOKE TEST] Initializing LLMPlanner with model: {model_name}")

    try:
        planner = LLMPlanner(model_name=model_name)
    except Exception as e:
        safe_msg = str(e).replace(api_key, "[REDACTED]")
        print(f"[SMOKE TEST] FAILED: Failed to initialize LLMPlanner: {safe_msg}")
        return 1

    scenario = create_checkout_scenario()
    registry = ToolRegistry([
        QueryMetricsTool(scenario),
        GrepLogsTool(scenario),
    ])

    h1 = Hypothesis(id="h1-db", statement="Database overload causes 502 errors")
    state = InvestigationState(
        incident=scenario.incident,
        hypotheses=[h1],
        investigation_status=InvestigationStatus.ACTIVE,
    )
    available_tools = registry.get_metadata()

    print("[SMOKE TEST] Sending single planning request to real Gemini API...")
    try:
        action = planner.plan_next_action(state, available_tools)
    except Exception as e:
        safe_msg = str(e).replace(api_key, "[REDACTED]")
        print(f"[SMOKE TEST] FAILED: Gemini API call failed: {safe_msg}")
        return 1

    if action is None:
        print("[SMOKE TEST] FAILED: Gemini returned null/empty action for active investigation.")
        return 1

    if not isinstance(action, PlannedAction):
        print(f"[SMOKE TEST] FAILED: Response is not a PlannedAction: {type(action)}")
        return 1

    if not registry.has(action.tool_name):
        print(
            f"[SMOKE TEST] FAILED: Tool '{action.tool_name}' is not registered."
        )
        return 1

    print(
        f"[SMOKE TEST] SUCCESS: Real Gemini ({planner.model_name}) returned valid PlannedAction:\n"
        f"  Tool: {action.tool_name}\n"
        f"  Purpose: {action.purpose}\n"
        f"  Parameters: {action.parameters}\n"
        f"  Hypothesis: {action.hypothesis_id}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(run_smoke_test())
