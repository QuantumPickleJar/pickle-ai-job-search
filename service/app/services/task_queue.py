"""Single-worker queued processing for local model tasks."""

from __future__ import annotations

import queue
import threading
from pathlib import Path

from app.config import Settings
from app.services.job_store import InvalidStoredDataError, JobStore, ResourceNotFoundError
from app.services.processing import ProcessingError, process_job
from app.services.task_store import TaskStore, TaskStoreError


class TaskManager:
    def __init__(
        self,
        settings: Settings,
        job_store: JobStore,
        task_store: TaskStore,
    ) -> None:
        self.settings = settings
        self.job_store = job_store
        self.task_store = task_store
        self._queue: queue.Queue[tuple[str, Path] | None] = queue.Queue()
        self._worker = threading.Thread(
            target=self._run,
            name="job-processing-worker",
            daemon=True,
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        for task in self.task_store.recover():
            self._enqueue_persisted(task)
        self._started = True
        self._worker.start()

    def stop(self) -> None:
        if not self._started:
            return
        self._queue.put(None)
        self._worker.join(timeout=5)

    def submit(self, job_id: str, job_path: Path) -> dict[str, object]:
        task = self.task_store.create(job_id)
        self._queue.put((str(task["task_id"]), job_path))
        return task

    def _enqueue_persisted(self, task: dict[str, object]) -> None:
        task_id = str(task["task_id"])
        job_id = str(task["job_id"])
        try:
            _, job_path = self.job_store.get_job(job_id)
        except (ResourceNotFoundError, InvalidStoredDataError):
            self.task_store.transition(
                task_id,
                "failed",
                error="Captured job is unavailable",
            )
            return
        self._queue.put((task_id, job_path))

    def _run(self) -> None:
        while True:
            work = self._queue.get()
            try:
                if work is None:
                    return
                task_id, job_path = work
                self._process(task_id, job_path)
            finally:
                self._queue.task_done()

    def _process(self, task_id: str, job_path: Path) -> None:
        try:
            self.task_store.transition(task_id, "running")
            app_dir = process_job(job_path, self.settings)
            self.task_store.transition(
                task_id,
                "succeeded",
                application_id=app_dir.name,
            )
        except ProcessingError:
            self._mark_failed(task_id, "Job processing failed; verify Ollama and service configuration")
        except (OSError, TaskStoreError):
            self._mark_failed(task_id, "Task processing failed")
        except Exception:
            self._mark_failed(task_id, "Task processing failed unexpectedly")

    def _mark_failed(self, task_id: str, message: str) -> None:
        try:
            task = self.task_store.get(task_id)
            if task["state"] not in {"succeeded", "failed"}:
                self.task_store.transition(task_id, "failed", error=message)
        except TaskStoreError:
            return
