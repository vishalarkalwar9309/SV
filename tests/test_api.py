"""Comprehensive tests for TRACE 2.0 FastAPI API boundary.

Covers:
- A. GET incidents returns all seeded incidents
- B. GET incidents never exposes ground_truth_root_cause
- C. POST investigation with valid incident creates investigation
- D. POST investigation returns unique investigation_id
- E. POST investigation initializes correct state
- F. POST unknown incident returns 404
- G. GET existing investigation returns current state
- H. GET unknown investigation returns 404
- I. POST /step executes exactly one step
- J. Multiple /step calls reflect updated state
- K. Terminal investigation cannot execute additional work
- L. Decision trace is exposed safely
- M. Ground truth never appears in any API response
- N. Hypothesis response does not contain fabricated confidence
- O. Evidence response preserves raw evidence information
- P. Invalid request produces 422
- Q. Unexpected application failure produces controlled response without secrets
- R. API import/app creation does not require real Gemini network access
- S. CORS behavior follows configured origin
- T. API serialization does not mutate InvestigationState
- Security: fake secret redaction & zero ground truth leakage
"""

from __future__ import annotations

from trace.agent.planner import MockPlanner, PlannedAction
from trace.api.app import create_app
from trace.application.investigation_service import InvestigationService, InvestigationStore
from trace.simulator.scenarios.checkout_502 import create as create_checkout_scenario
from unittest.mock import MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient


@pytest.fixture
def test_app():
    """Fixture providing a FastAPI application with deterministic mock planner."""
    store = InvestigationStore()
    # Default test planner that can be overridden
    planner = MockPlanner([
        PlannedAction(
            tool_name="query_metrics",
            parameters={"service": "checkout-db"},
            purpose="Query DB metrics",
            hypothesis_id="h1-db",
        )
    ])
    service = InvestigationService(store=store, planner=planner)
    app = create_app(service=service)
    return app


@pytest.fixture
def client(test_app):
    """Fixture providing a TestClient configured with test_app."""
    return TestClient(test_app)


class TestIncidentsEndpoint:
    """Requirement A, B: Incidents listing and ground-truth isolation."""

    def test_get_incidents_returns_all_seeded_incidents(self, client):
        response = client.get("/api/incidents")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        checkout = next((inc for inc in data if inc["id"] == "checkout_502"), None)
        assert checkout is not None
        assert checkout["title"] == "Checkout 502 Errors"
        assert checkout["service"] == "checkout"
        assert checkout["severity"] == "high"
        assert "502" in checkout["initial_observation"]
        assert "timestamp" in checkout

    def test_get_incidents_never_exposes_ground_truth(self, client):
        scenario = create_checkout_scenario()
        secret_ground_truth = scenario.incident.ground_truth_root_cause
        assert secret_ground_truth is not None

        response = client.get("/api/incidents")
        assert response.status_code == status.HTTP_200_OK

        # 1. Field name must not exist
        for inc in response.json():
            assert "ground_truth_root_cause" not in inc
            assert "ground_truth" not in inc

        # 2. Secret text must not appear anywhere in raw response body
        assert secret_ground_truth not in response.text


class TestInvestigationsLifecycle:
    """Requirement C, D, E, F, G, H: Creation and retrieval of investigations."""

    def test_post_investigation_valid_incident_creates_investigation(self, client):
        # Test both hyphen and underscore formats
        for incident_id in ("checkout-502", "checkout_502"):
            response = client.post("/api/investigations", json={"incident_id": incident_id})
            assert response.status_code == status.HTTP_201_CREATED
            data = response.json()

            assert "investigation_id" in data
            assert data["investigation_id"].startswith("inv-")
            assert data["status"] == "not_started"
            assert data["incident"]["id"] == "checkout_502"
            assert isinstance(data["hypotheses"], list)
            assert len(data["hypotheses"]) == 2
            assert data["evidence"] == []
            assert data["actions_taken"] == []
            assert data["decision_trace"] == []

    def test_post_investigation_returns_unique_investigation_ids(self, client):
        r1 = client.post("/api/investigations", json={"incident_id": "checkout-502"})
        r2 = client.post("/api/investigations", json={"incident_id": "checkout-502"})
        assert r1.status_code == status.HTTP_201_CREATED
        assert r2.status_code == status.HTTP_201_CREATED

        id1 = r1.json()["investigation_id"]
        id2 = r2.json()["investigation_id"]
        assert id1 != id2

    def test_post_unknown_incident_returns_404(self, client):
        response = client.post(
            "/api/investigations", json={"incident_id": "unknown-ghost-incident"}
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in response.json()["detail"].lower()

    def test_get_existing_investigation_returns_current_state(self, client):
        create_res = client.post("/api/investigations", json={"incident_id": "checkout-502"})
        inv_id = create_res.json()["investigation_id"]

        get_res = client.get(f"/api/investigations/{inv_id}")
        assert get_res.status_code == status.HTTP_200_OK
        data = get_res.json()
        assert data["investigation_id"] == inv_id
        assert data["status"] == "not_started"
        assert len(data["hypotheses"]) == 2

    def test_get_unknown_investigation_returns_404(self, client):
        response = client.get("/api/investigations/inv-nonexistent-404")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in response.json()["detail"].lower()


class TestOneStepExecution:
    """Requirement I, J, K: Single-step execution, multi-step progress, and terminal guard."""

    def test_post_step_executes_exactly_one_step(self):
        # Configure planner with 2 distinct planned actions
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Step 1 purpose",
                hypothesis_id="h1-db",
            ),
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": "connection"},
                purpose="Step 2 purpose",
                hypothesis_id="h2-leak",
            ),
        ])
        service = InvestigationService(planner=planner)
        app = create_app(service=service)
        client = TestClient(app)

        # 1. Create investigation
        init_res = client.post("/api/investigations", json={"incident_id": "checkout-502"})
        inv_id = init_res.json()["investigation_id"]

        # 2. Execute exactly ONE step
        step_res = client.post(f"/api/investigations/{inv_id}/step")
        assert step_res.status_code == status.HTTP_200_OK
        data = step_res.json()

        # Exactly 1 action, 1 evidence item, 1 decision trace step executed
        assert len(data["actions_taken"]) == 1
        assert data["actions_taken"][0]["tool_name"] == "query_metrics"
        assert data["actions_taken"][0]["purpose"] == "Step 1 purpose"

        assert len(data["evidence"]) == 1
        assert data["evidence"][0]["source_tool"] == "query_metrics"
        assert data["evidence"][0]["tool_succeeded"] is True

        assert len(data["decision_trace"]) == 1
        assert data["decision_trace"][0]["step_number"] == 1
        assert data["decision_trace"][0]["action"] == "query_metrics"

    def test_multiple_step_calls_reflect_cumulative_state(self):
        # 3 actions reaching resolution:
        # Step 1: query_metrics -> disproves H1
        # Step 2: grep_logs -> supports H2
        # Step 3: fetch_recent_commits -> confirms H2 -> reaches RESOLVED
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Test H1",
                hypothesis_id="h1-db",
            ),
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": "connection"},
                purpose="Test H2",
                hypothesis_id="h2-leak",
            ),
            PlannedAction(
                tool_name="fetch_recent_commits",
                parameters={"service": "checkout", "limit": 5},
                purpose="Confirm H2",
                hypothesis_id="h2-leak",
            ),
        ])
        service = InvestigationService(planner=planner)
        app = create_app(service=service)
        client = TestClient(app)

        init_res = client.post("/api/investigations", json={"incident_id": "checkout-502"})
        inv_id = init_res.json()["investigation_id"]

        # Step 1
        r1 = client.post(f"/api/investigations/{inv_id}/step")
        d1 = r1.json()
        assert len(d1["actions_taken"]) == 1
        h1_after_s1 = next(h for h in d1["hypotheses"] if h["id"] == "h1-db")
        assert h1_after_s1["status"] == "disproven"

        # Step 2
        r2 = client.post(f"/api/investigations/{inv_id}/step")
        d2 = r2.json()
        assert len(d2["actions_taken"]) == 2

        # Step 3
        r3 = client.post(f"/api/investigations/{inv_id}/step")
        d3 = r3.json()
        assert len(d3["actions_taken"]) == 3
        assert d3["status"] == "resolved"
        h2_after_s3 = next(h for h in d3["hypotheses"] if h["id"] == "h2-leak")
        assert h2_after_s3["status"] == "supported"

    def test_terminal_investigation_cannot_execute_additional_work(self):
        # Planner with extra actions after reaching resolution
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Disprove H1",
                hypothesis_id="h1-db",
            ),
            PlannedAction(
                tool_name="fetch_recent_commits",
                parameters={"service": "checkout", "limit": 5},
                purpose="Confirm H2 directly",
                hypothesis_id="h2-leak",
            ),
            PlannedAction(
                tool_name="grep_logs",
                parameters={"service": "checkout", "pattern": "error"},
                purpose="Unneeded extra action after resolution",
                hypothesis_id="h2-leak",
            ),
        ])
        service = InvestigationService(planner=planner)
        app = create_app(service=service)
        client = TestClient(app)

        init_res = client.post("/api/investigations", json={"incident_id": "checkout-502"})
        inv_id = init_res.json()["investigation_id"]

        # Execute step 1 and step 2 -> resolves
        client.post(f"/api/investigations/{inv_id}/step")
        res2 = client.post(f"/api/investigations/{inv_id}/step")
        assert res2.json()["status"] == "resolved"
        assert len(res2.json()["actions_taken"]) == 2

        # Step 3 attempt: should NOT execute any additional action
        res3 = client.post(f"/api/investigations/{inv_id}/step")
        assert res3.status_code == status.HTTP_200_OK
        assert res3.json()["status"] == "resolved"
        assert len(res3.json()["actions_taken"]) == 2
        assert len(res3.json()["evidence"]) == 2

    def test_step_on_unknown_investigation_returns_404(self, client):
        res = client.post("/api/investigations/inv-missing-id/step")
        assert res.status_code == status.HTTP_404_NOT_FOUND


class TestViewModelsAndFieldIntegrity:
    """Requirement L, M, N, O, T: Decision trace, confidence, raw evidence, immutability."""

    def test_decision_trace_is_exposed_safely_without_hidden_reasoning(self):
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Inspect metrics",
                hypothesis_id="h1-db",
            )
        ])
        service = InvestigationService(planner=planner)
        app = create_app(service=service)
        client = TestClient(app)

        inv_id = client.post(
            "/api/investigations", json={"incident_id": "checkout-502"}
        ).json()["investigation_id"]
        step_data = client.post(f"/api/investigations/{inv_id}/step").json()

        trace = step_data["decision_trace"]
        assert len(trace) == 1
        step0 = trace[0]

        # Allowed structured fields
        assert set(step0.keys()) == {
            "step_number",
            "action",
            "purpose",
            "observation",
            "evaluation",
            "hypothesis_status",
            "next_action",
        }
        # Prohibited fields
        assert "chain_of_thought" not in step0
        assert "thought" not in step0
        assert "reasoning" not in step0

    def test_hypothesis_response_does_not_contain_fabricated_confidence(self, client):
        res = client.post("/api/investigations", json={"incident_id": "checkout-502"})
        hypotheses = res.json()["hypotheses"]
        assert len(hypotheses) > 0

        for h in hypotheses:
            assert "confidence" not in h
            assert "confidence_score" not in h
            assert "probability" not in h
            assert "score" not in h

    def test_evidence_response_preserves_raw_evidence_without_invented_fields(self):
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Query metrics",
            )
        ])
        service = InvestigationService(planner=planner)
        app = create_app(service=service)
        client = TestClient(app)

        inv_id = client.post(
            "/api/investigations", json={"incident_id": "checkout-502"}
        ).json()["investigation_id"]
        data = client.post(f"/api/investigations/{inv_id}/step").json()

        ev = data["evidence"][0]
        assert "id" in ev
        assert ev["source_tool"] == "query_metrics"
        assert isinstance(ev["query"], dict)
        assert isinstance(ev["raw_data"], dict)
        assert ev["tool_succeeded"] is True
        assert "timestamp" in ev

    def test_api_serialization_does_not_mutate_domain_state(self):
        planner = MockPlanner([
            PlannedAction(
                tool_name="query_metrics",
                parameters={"service": "checkout-db"},
                purpose="Query metrics",
            )
        ])
        store = InvestigationStore()
        service = InvestigationService(store=store, planner=planner)
        app = create_app(service=service)
        client = TestClient(app)

        inv_id = client.post(
            "/api/investigations", json={"incident_id": "checkout-502"}
        ).json()["investigation_id"]
        session = store.get(inv_id)
        assert session is not None

        orig_hyp_count = len(session.state.hypotheses)
        orig_incident_title = session.state.incident.title

        # Serialization through GET
        res = client.get(f"/api/investigations/{inv_id}")
        assert res.status_code == status.HTTP_200_OK

        # Domain state unaffected
        assert len(session.state.hypotheses) == orig_hyp_count
        assert session.state.incident.title == orig_incident_title


class TestValidationAndSecurity:
    """Requirement P, Q, R, S, 15: Input validation, secret safety, CORS, and network-free setup."""

    def test_invalid_request_produces_422(self, client):
        # Missing required incident_id
        r1 = client.post("/api/investigations", json={})
        assert r1.status_code == 422

        # Empty string incident_id
        r2 = client.post("/api/investigations", json={"incident_id": ""})
        assert r2.status_code == 422

    def test_unexpected_failure_returns_500_without_secrets(self, monkeypatch):
        fake_secret = "TEST_FAKE_GEMINI_KEY_123"
        monkeypatch.setenv("GOOGLE_API_KEY", fake_secret)

        # Service with a mock controller that explodes with an exception containing fake secret
        mock_controller = MagicMock()
        mock_controller.step.side_effect = RuntimeError(f"Fatal explosion with key {fake_secret}")

        store = InvestigationStore()
        service = InvestigationService(store=store)
        app = create_app(service=service)
        client = TestClient(app, raise_server_exceptions=False)

        # Create session manually with exploding controller
        scenario = create_checkout_scenario()
        from trace.application.investigation_service import InvestigationSession
        from trace.engine.state import InvestigationState, InvestigationStatus

        session = InvestigationSession(
            investigation_id="inv-exploding",
            state=InvestigationState(
                incident=scenario.incident,
                investigation_status=InvestigationStatus.ACTIVE,
            ),
            controller=mock_controller,
            scenario=scenario,
        )
        store.save(session)

        response = client.post("/api/investigations/inv-exploding/step")
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        # Fake secret MUST NOT leak in 500 error response body
        assert fake_secret not in response.text
        assert "Internal server error" in response.text

    def test_app_creation_does_not_require_gemini_network(self, monkeypatch):
        # Clear GOOGLE_API_KEY to verify clean zero-network app creation
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        fresh_app = create_app()
        test_client = TestClient(fresh_app)

        # GET incidents should work completely without network or API keys
        res = test_client.get("/api/incidents")
        assert res.status_code == status.HTTP_200_OK

    def test_cors_behavior_follows_configured_origin(self, monkeypatch):
        test_origin = "http://frontend.local:5173"
        monkeypatch.setenv("TRACE_FRONTEND_ORIGIN", test_origin)

        cors_app = create_app()
        cors_client = TestClient(cors_app)

        # Request with configured origin
        allowed_res = cors_client.get(
            "/api/incidents",
            headers={"Origin": test_origin},
        )
        assert allowed_res.status_code == status.HTTP_200_OK
        assert allowed_res.headers.get("access-control-allow-origin") == test_origin

        # Request with disallowed origin
        disallowed_res = cors_client.get(
            "/api/incidents",
            headers={"Origin": "http://evil-site.com"},
        )
        assert disallowed_res.headers.get("access-control-allow-origin") != "http://evil-site.com"

    def test_ground_truth_never_appears_in_investigation_responses(self, client):
        scenario = create_checkout_scenario()
        secret_ground_truth = scenario.incident.ground_truth_root_cause
        assert secret_ground_truth is not None

        # Check in create response
        create_res = client.post("/api/investigations", json={"incident_id": "checkout-502"})
        assert secret_ground_truth not in create_res.text
        assert "ground_truth_root_cause" not in create_res.text

        inv_id = create_res.json()["investigation_id"]

        # Check in get response
        get_res = client.get(f"/api/investigations/{inv_id}")
        assert secret_ground_truth not in get_res.text
        assert "ground_truth_root_cause" not in get_res.text

        # Check in step response
        step_res = client.post(f"/api/investigations/{inv_id}/step")
        assert secret_ground_truth not in step_res.text
        assert "ground_truth_root_cause" not in step_res.text
