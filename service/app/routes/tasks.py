"""Task status API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings, get_settings
from app.services.task_store import InvalidTaskDataError, TaskNotFoundError, TaskStore


router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_task_store(settings: Settings = Depends(get_settings)) -> TaskStore:
    return TaskStore(settings.app_data_dir)


@router.get("")
def list_tasks(store: TaskStore = Depends(get_task_store)) -> dict[str, Any]:
    try:
        tasks = store.list()
    except InvalidTaskDataError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"tasks": tasks, "count": len(tasks)}


@router.get("/{task_id}")
def get_task(task_id: str, store: TaskStore = Depends(get_task_store)) -> dict[str, Any]:
    try:
        return store.get(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTaskDataError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
