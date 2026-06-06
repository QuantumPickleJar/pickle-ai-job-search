"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import SERVICE_NAME, SERVICE_VERSION, get_settings
from app.routes.applications import router as applications_router
from app.routes.health import router as health_router
from app.routes.jobs import router as jobs_router
from app.routes.tasks import router as tasks_router
from app.routes.ui import router as ui_router
from app.services.job_store import JobStore
from app.services.task_queue import TaskManager
from app.services.task_store import TaskStore


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    manager = TaskManager(
        settings=settings,
        job_store=JobStore(settings.app_data_dir),
        task_store=TaskStore(settings.app_data_dir),
    )
    app.state.task_manager = manager
    manager.start()
    try:
        yield
    finally:
        manager.stop()


app = FastAPI(
    title=SERVICE_NAME,
    version=SERVICE_VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.include_router(health_router)
app.include_router(jobs_router)
app.include_router(applications_router)
app.include_router(tasks_router)
app.include_router(ui_router)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="static",
)


def run() -> None:
    settings = get_settings()
    uvicorn.run(app, host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    run()
