"""Application workspace API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_job_store
from app.services.job_store import (
    InvalidStoredDataError,
    JobStore,
    ResourceNotFoundError,
)


router = APIRouter(prefix="/applications", tags=["applications"])


@router.get("")
def list_applications(store: JobStore = Depends(get_job_store)) -> dict[str, Any]:
    try:
        applications = store.list_applications()
    except InvalidStoredDataError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"applications": applications, "count": len(applications)}


@router.get("/{application_id}")
def get_application(
    application_id: str,
    store: JobStore = Depends(get_job_store),
) -> dict[str, Any]:
    try:
        return store.get_application(application_id)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidStoredDataError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
