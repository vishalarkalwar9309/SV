# TRACE 2.0 — Workspace Audit Report

**Date:** 2026-09-09  
**Auditor:** Implementation Engineer

---

## A. Current Workspace State

| Property | Value |
|---|---|
| **Root** | `c:\Users\visha\OneDrive\Desktop\SV` |
| **Git** | ❌ Not initialized |
| **Virtual environment** | ❌ None detected |
| **Total files** | 1 |
| **Project scaffolding** | None — greenfield |

> [!WARNING]
> The workspace is on **OneDrive**. File-sync can cause lock conflicts with SQLite, `__pycache__`, and `.git` index. Consider adding a `.gitignore` early and testing that `sqlite3` writes don't conflict with OneDrive sync. If issues arise, moving to a non-synced folder is the safest fix.

---

## B. Existing Files

| File | Purpose |
|---|---|
| [TRACE_2.0_Incident_Investigation_Agent (1).docx](file:///c:/Users/visha/OneDrive/Desktop/SV/TRACE_2.0_Incident_Investigation_Agent%20(1).docx) | Project specification / design document (14 KB) |

No source code, no configuration, no tests exist yet.

---

## C. Existing Dependencies (System-wide pip)

### Directly relevant (already installed globally)

| Package | Version | Use in TRACE |
|---|---|---|
| `fastapi` | 0.121.2 | API layer |
| `uvicorn` | 0.38.0 | ASGI server |
| `pydantic` | 2.12.4 | Data models, structured output |
| `httpx` | 0.28.1 | Async HTTP (tool simulation) |
| `aiosqlite` | 0.22.1 | Async SQLite |
| `pytest` | 9.1.1 | Testing |
| `pytest-asyncio` | 1.4.0 | Async test support |
| `python-dotenv` | 1.2.1 | Environment config |
| `google-genai` | 2.8.0 | Gemini SDK |
| `google-generativeai` | 0.8.6 | Gemini SDK (legacy) |

### Not yet installed (will need)

| Package | Purpose | When |
|---|---|---|
| `langgraph` | Agent orchestration | Phase 2+ (after core is tested) |
| `ruff` | Linting/formatting | Day 1 |

---

## D. Runtime Versions

| Runtime | Version |
|---|---|
| **Python** | 3.11.9 |
| **Node.js** | 24.19.0 |
| **npm** | 11.17.0 |
| **pip** | 26.2.1 |

> [!NOTE]
> Python 3.11 is ideal — full `tomllib` support, stable `asyncio`, and compatible with all target dependencies.

---

## E. Existing Configuration

**None.** No `pyproject.toml`, `requirements.txt`, `.env`, `.gitignore`, `Makefile`, or `ruff.toml` exist.

---

## F. Recommended Minimal Project Structure

```
trace/                          ← workspace root (SV/)
├── pyproject.toml              ← deps, project metadata, ruff config
├── .env                        ← API keys (gitignored)
├── .gitignore
├── README.md
│
├── src/
│   └── trace/
│       ├── __init__.py
│       │
│       ├── models/             ← Pydantic domain models
│       │   ├── __init__.py
│       │   ├── incident.py     ← Incident, Severity
│       │   ├── hypothesis.py   ← Hypothesis, HypothesisStatus
│       │   ├── evidence.py     ← Evidence, EvidenceItem
│       │   └── action.py       ← AgentAction, ActionResult
│       │
│       ├── simulator/          ← Deterministic seeded scenarios
│       │   ├── __init__.py
│       │   ├── registry.py     ← Scenario registry & loader
│       │   └── scenarios/
│       │       ├── __init__.py
│       │       ├── checkout_502.py
│       │       ├── memory_leak.py
│       │       └── dns_timeout.py
│       │
│       ├── tools/              ← Investigation tools (deterministic)
│       │   ├── __init__.py
│       │   ├── base.py         ← BaseTool ABC
│       │   ├── grep_logs.py
│       │   ├── query_metrics.py
│       │   └── fetch_commits.py
│       │
│       ├── evaluator/          ← Deterministic evidence evaluator
│       │   ├── __init__.py
│       │   └── evaluator.py
│       │
│       ├── engine/             ← Agent controller / orchestration
│       │   ├── __init__.py
│       │   ├── controller.py   ← Main investigation loop
│       │   ├── planner.py      ← LLM-backed planner
│       │   └── state.py        ← Investigation state
│       │
│       └── api/                ← FastAPI endpoints (Phase 2)
│           ├── __init__.py
│           └── routes.py
│
└── tests/
    ├── __init__.py
    ├── test_models.py
    ├── test_simulator.py
    ├── test_tools.py
    ├── test_evaluator.py
    └── test_engine.py
```

> [!IMPORTANT]
> **Key structural principle:** Every component below the `engine/` layer (`models`, `simulator`, `tools`, `evaluator`) must be fully testable **without an LLM**. The LLM dependency is isolated to `engine/planner.py`.

---

## G. Recommended Minimal Dependencies

### `pyproject.toml` — MVP only

```
[project]
requires-python = ">=3.11"

dependencies = [
    "pydantic>=2.12",       # Domain models + structured LLM output
    "google-genai>=2.8",    # Gemini function calling
    "python-dotenv>=1.0",   # .env loading
]

[project.optional-dependencies]
api = [
    "fastapi>=0.121",
    "uvicorn[standard]>=0.38",
]
dev = [
    "pytest>=9.0",
    "pytest-asyncio>=1.0",
    "ruff>=0.11",
]
```

**What we intentionally exclude from MVP:**

| Package | Why excluded |
|---|---|
| `langgraph` | Add only after core loop is proven without it |
| `sqlalchemy` / `alembic` | Overkill — `json` files or `aiosqlite` direct is enough |
| `langchain` | Not needed; raw Gemini SDK with function calling is simpler |
| `celery` / `dramatiq` | No async job queue needed for MVP |
| Any frontend framework | Backend-first; demo via CLI/pytest/Swagger |

---

## H. Recommended Development Sequence

### Day 1 — Foundation (no LLM)

| # | Task | Deliverable |
|---|---|---|
| 1 | `git init`, `pyproject.toml`, `.gitignore`, `.env` | Repo scaffolded |
| 2 | Pydantic models (`Incident`, `Hypothesis`, `Evidence`, `Action`) | `src/trace/models/` |
| 3 | `BaseTool` ABC + 3 tool stubs (`grep_logs`, `query_metrics`, `fetch_commits`) | `src/trace/tools/` |
| 4 | Simulator: `checkout_502` scenario with ground truth | `src/trace/simulator/` |
| 5 | Wire tools to simulator so calling `grep_logs("checkout")` returns seeded data | Tools return real data |
| 6 | Tests for all of the above | `tests/` green |

### Day 2 — Evaluator + Engine (no LLM yet)

| # | Task | Deliverable |
|---|---|---|
| 7 | Deterministic evaluator: rules that match evidence → hypothesis status | `src/trace/evaluator/` |
| 8 | Investigation state object (hypotheses list, evidence store, action log) | `src/trace/engine/state.py` |
| 9 | Hard-coded controller that runs the checkout_502 scenario end-to-end using a **scripted** action sequence (no LLM) | Prove the full pipeline works deterministically |
| 10 | Tool failure simulation (503 on `fetch_commits`) + evaluator handling | Failure scenario tested |
| 11 | Tests for evaluator + scripted controller | `tests/` green |

### Day 3 — LLM Integration

| # | Task | Deliverable |
|---|---|---|
| 12 | Gemini function-calling planner: given state → pick next tool + params | `src/trace/engine/planner.py` |
| 13 | Replace scripted controller with LLM-driven loop | Agent runs the demo scenario autonomously |
| 14 | Add 2nd and 3rd seeded scenarios | Broader test coverage |
| 15 | Agent Decision Trace output (safe, no CoT leakage) | Structured JSON trace |

### Day 4 — API + Polish

| # | Task | Deliverable |
|---|---|---|
| 16 | FastAPI: `POST /investigate`, `GET /investigation/{id}`, `POST /approve-remediation/{id}` | `src/trace/api/` |
| 17 | Human-approval gate for remediation | Approval flow works |
| 18 | Swagger-based demo walkthrough | Demo-ready backend |

### Day 5 — Demo Prep + Stretch

| # | Task | Deliverable |
|---|---|---|
| 19 | Frontend (minimal; decision trace viewer) | If time allows |
| 20 | LangGraph migration (if desired) | Optional |
| 21 | End-to-end rehearsal, edge cases, presentation prep | Demo polished |

---

## I. Potential Technical Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **OneDrive sync + SQLite** | Write conflicts, locked DB | Use JSON for MVP storage; test early |
| **Gemini function-calling reliability** | LLM may not follow tool schema consistently | Pydantic validation on every LLM output; retry with re-prompt |
| **Gemini rate limits / quota** | Blocked during demo | Cache LLM responses for known scenarios; have a fallback scripted mode |
| **Scope creep** | 5 days is tight for 2 people | Ruthlessly cut to 3 scenarios, 3 tools, no frontend in MVP |
| **No virtualenv** | Global pip pollution, version conflicts | Create a venv on Day 1 |
| **LLM inventing evidence** | Violates core architectural principle | Never pass raw LLM output as evidence; all evidence must come from tools; evaluator only accepts `ToolResult` objects |
| **Evaluator too simplistic** | Doesn't generalize beyond seeded scenarios | Acceptable for MVP; document as known limitation |
| **pytest-asyncio 1.4.0** | Very old; may have compatibility issues with newer pytest | Pin or upgrade on Day 1 |

---

## J. What NOT to Build in the MVP

| Do NOT build | Reason |
|---|---|
| Real infrastructure integrations (Datadog, PagerDuty, etc.) | Simulator is sufficient for demo |
| Multi-user auth / RBAC | Single-user demo |
| Persistent database with migrations | JSON/SQLite raw is enough |
| WebSocket real-time streaming | Polling or SSE at most |
| Complex frontend SPA | Swagger UI or a single-page trace viewer max |
| LangGraph state machine | Build raw loop first; migrate only if time permits |
| More than 3 seeded scenarios | Diminishing returns |
| CI/CD pipeline | Manual testing is fine for a hackathon |
| Observability/tracing (OpenTelemetry) | Adds complexity with no demo value |
| Containerization (Docker) | Run locally |
| Caching layer (Redis) | Not needed at this scale |

---

## K. Exact First Implementation Task

> **Task 1: Project scaffolding + Pydantic domain models**

Specifically:

1. `git init`
2. Create `pyproject.toml` with project metadata + dependencies
3. Create `.gitignore` (Python, `.env`, `__pycache__`, OneDrive temp files)
4. Create `.env.example` with `GOOGLE_API_KEY=`
5. Create `src/trace/__init__.py`
6. Create `src/trace/models/`:
   - `incident.py` — `Incident(id, title, service, severity, initial_observation, timestamp)`
   - `hypothesis.py` — `Hypothesis(id, statement, status, supporting_evidence, contradicting_evidence)` with `HypothesisStatus` enum (`PROPOSED`, `INVESTIGATING`, `SUPPORTED`, `DISPROVEN`, `INSUFFICIENT`)
   - `evidence.py` — `EvidenceItem(id, source_tool, query, raw_data, timestamp)` + `EvidenceStore`
   - `action.py` — `AgentAction(tool_name, params, purpose)` + `ActionResult(success, data, error)`
7. Create `tests/test_models.py` with basic instantiation + validation tests
8. Run `pytest` — verify green

**Estimated time:** ~1 hour  
**Dependency on LLM:** None  
**Blocks:** Everything else

---

> [!TIP]
> **First thing to do before Task 1:** Create a Python virtual environment to isolate dependencies from the global install.
> ```powershell
> python -m venv .venv
> .\.venv\Scripts\Activate.ps1
> ```
