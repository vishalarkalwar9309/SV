"""FastAPI route definitions for TRACE 2.0.

Thin route layer that delegates all business logic to InvestigationService.
"""

from __future__ import annotations

from trace.api.schemas import (
    CreateInvestigationRequest,
    IncidentResponse,
    InvestigationResponse,
)
from trace.application.investigation_service import (
    IncidentNotFoundError,
    InvestigationNotFoundError,
    InvestigationService,
)

from fastapi import APIRouter, Depends, HTTPException, Request, status

router = APIRouter(prefix="/api", tags=["Investigations"])


def get_investigation_service(request: Request) -> InvestigationService:
    """FastAPI dependency to retrieve InvestigationService from app state."""
    return request.app.state.investigation_service


@router.get(
    "/incidents",
    response_model=list[IncidentResponse],
    summary="List all available deterministic incidents",
)
def list_incidents(
    service: InvestigationService = Depends(get_investigation_service),
) -> list[IncidentResponse]:
    """Return all available deterministic simulator incidents.

    Omits internal simulator metadata such as ground_truth_root_cause.
    """
    return service.get_all_incidents()


@router.post(
    "/investigations",
    response_model=InvestigationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new investigation session",
)
def create_investigation(
    request: CreateInvestigationRequest,
    service: InvestigationService = Depends(get_investigation_service),
) -> InvestigationResponse:
    """Create a fresh investigation state for the specified incident.

    Initializes the state and stores it without executing agent steps.
    """
    try:
        return service.create_investigation(request.incident_id)
    except IncidentNotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err


@router.get(
    "/investigations/{investigation_id}",
    response_model=InvestigationResponse,
    summary="Get current investigation state",
)
def get_investigation(
    investigation_id: str,
    service: InvestigationService = Depends(get_investigation_service),
) -> InvestigationResponse:
    """Retrieve the current state of an ongoing or completed investigation."""
    try:
        return service.get_investigation(investigation_id)
    except InvestigationNotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err


@router.post(
    "/investigations/{investigation_id}/step",
    response_model=InvestigationResponse,
    summary="Execute one investigation step",
)
def step_investigation(
    investigation_id: str,
    service: InvestigationService = Depends(get_investigation_service),
) -> InvestigationResponse:
    """Execute exactly one agent controller step and return the updated state."""
    try:
        return service.step(investigation_id)
    except InvestigationNotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err
