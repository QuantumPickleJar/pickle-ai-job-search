"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.config import Settings, get_settings
from app.services.job_store import JobStore
from app.services.task_queue import TaskManager


def get_job_store(settings: Settings = Depends(get_settings)) -> JobStore:
    return JobStore(settings.app_data_dir)


def get_task_manager(request: Request) -> TaskManager:
    manager = getattr(request.app.state, "task_manager", None)
    if not isinstance(manager, TaskManager):
        raise HTTPException(status_code=503, detail="Task processor is unavailable")
    return manager
