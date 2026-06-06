"""Captured job API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from ai_job_search.intake import JobIntakeError

from app.auth import require_api_key
from app.dependencies import get_job_store, get_task_manager
from app.services.job_store import (
    InvalidStoredDataError,
    JobStore,
    ResourceNotFoundError,
)
from app.services.task_queue import TaskManager


router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/capture", status_code=status.HTTP_201_CREATED)
def capture_job(
    payload: dict[str, Any],
    _: None = Depends(require_api_key),
    store: JobStore = Depends(get_job_store),
) -> dict[str, Any]:
    try:
        job, path = store.save_capture(payload)
    except JobIntakeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InvalidStoredDataError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to save captured job") from exc

    return {
        "status": "saved",
        "id": job["id"],
        "path": store.relative_path(path),
        "job": job,
    }


@router.get("")
def list_jobs(store: JobStore = Depends(get_job_store)) -> dict[str, Any]:
    try:
        jobs = store.list_jobs()
    except InvalidStoredDataError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"jobs": jobs, "count": len(jobs)}


@router.get("/{job_id}")
def get_job(job_id: str, store: JobStore = Depends(get_job_store)) -> dict[str, Any]:
    try:
        job, _ = store.get_job(job_id)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidStoredDataError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return job


@router.post("/{job_id}/process", status_code=status.HTTP_202_ACCEPTED)
def process_captured_job(
    job_id: str,
    _: None = Depends(require_api_key),
    store: JobStore = Depends(get_job_store),
    manager: TaskManager = Depends(get_task_manager),
) -> dict[str, Any]:
    try:
        _, job_path = store.get_job(job_id)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidStoredDataError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    task = manager.submit(job_id, job_path)
    return {
        "status": "queued",
        "job_id": job_id,
        "task_id": task["task_id"],
    }
