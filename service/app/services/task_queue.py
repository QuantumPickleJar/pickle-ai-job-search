"""Single-worker queued processing for local model tasks."""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.job_store import InvalidStoredDataError, JobStore, ResourceNotFoundError
from app.services.task_errors import safe_task_error
from app.services.processing import ProcessingError, generate_cover_letter, generate_cv, process_job
from app.services.task_store import TaskStore, TaskStoreError

logger = logging.getLogger(__name__)


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
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
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
        task = self.task_store.create(job_id, task_type="process-job")
        self._queue.put(
            {
                "task_id": str(task["task_id"]),
                "task_type": "process-job",
                "job_path": job_path,
            }
        )
        return task

    def submit_application_task(self, application_id: str, task_type: str) -> dict[str, object]:
        task = self.task_store.create(
            application_id,
            task_type=task_type,
            application_id=application_id,
        )
        self._queue.put(
            {
                "task_id": str(task["task_id"]),
                "task_type": task_type,
                "application_id": application_id,
            }
        )
        return task

    def _enqueue_persisted(self, task: dict[str, object]) -> None:
        task_id = str(task["task_id"])
        job_id = str(task["job_id"])
        task_type = str(task.get("task_type", "process-job"))
        if task_type in {"generate-cv", "generate-cover-letter"}:
            application_id = str(task.get("application_id") or job_id)
            self._queue.put(
                {
                    "task_id": task_id,
                    "task_type": task_type,
                    "application_id": application_id,
                }
            )
            return
        try:
            _, job_path = self.job_store.get_job(job_id)
        except (ResourceNotFoundError, InvalidStoredDataError):
            self.task_store.transition(
                task_id,
                "failed",
                error="Captured job is unavailable",
            )
            return
        self._queue.put(
            {
                "task_id": task_id,
                "task_type": "process-job",
                "job_path": job_path,
            }
        )

    def _run(self) -> None:
        while True:
            work = self._queue.get()
            try:
                if work is None:
                    return
                self._process(work)
            finally:
                self._queue.task_done()

    def _process(self, work: dict[str, Any]) -> None:
        task_id = str(work["task_id"])
        task_type = str(work["task_type"])
        try:
            self.task_store.transition(task_id, "running")
            if task_type == "process-job":
                self.task_store.set_pipeline_stage(task_id, "fit-analysis")
                job_path = Path(str(work["job_path"]))
                app_dir = process_job(job_path, self.settings)
                self.task_store.set_pipeline_stage(task_id, "persist-results")
                self.task_store.transition(
                    task_id,
                    "succeeded",
                    application_id=app_dir.name,
                )
            elif task_type == "generate-cv":
                application_id = str(work["application_id"])
                self.task_store.record_model_query(task_id, "cv-draft")
                generate_cv(application_id, self.settings)
                self.task_store.set_pipeline_stage(task_id, "cv-finalized")
                self.task_store.transition(
                    task_id,
                    "succeeded",
                    application_id=application_id,
                )
            elif task_type == "generate-cover-letter":
                application_id = str(work["application_id"])
                self.task_store.set_pipeline_stage(task_id, "build-brief")
                generate_cover_letter(
                    application_id,
                    self.settings,
                    query_callback=lambda stage: self.task_store.record_model_query(task_id, stage),
                )
                self.task_store.set_pipeline_stage(task_id, "finalize-letter")
                self.task_store.transition(
                    task_id,
                    "succeeded",
                    application_id=application_id,
                )
            else:
                raise TaskStoreError(f"unsupported task type: {task_type}")
        except ProcessingError as exc:
            sanitized = safe_task_error(exc, task_type)
            self._log_task_failure(
                task_id=task_id,
                task_type=task_type,
                sanitized_error=sanitized,
            )
            self._mark_failed(task_id, sanitized)
        except OSError as exc:
            sanitized = safe_task_error(exc, task_type)
            self._log_task_failure(
                task_id=task_id,
                task_type=task_type,
                sanitized_error=sanitized,
            )
            self._mark_failed(task_id, sanitized)
        except TaskStoreError as exc:
            sanitized = safe_task_error(exc, task_type)
            self._log_task_failure(
                task_id=task_id,
                task_type=task_type,
                sanitized_error=sanitized,
            )
            self._mark_failed(task_id, sanitized)
        except Exception as exc:
            sanitized = safe_task_error(exc, task_type)
            self._log_task_failure(
                task_id=task_id,
                task_type=task_type,
                sanitized_error=sanitized,
            )
            self._mark_failed(task_id, sanitized)

    def _log_task_failure(self, *, task_id: str, task_type: str, sanitized_error: str) -> None:
        job_id = "unknown"
        application_id = None
        try:
            task = self.task_store.get(task_id)
            job_id = str(task.get("job_id") or "unknown")
            application_id = task.get("application_id")
        except TaskStoreError:
            pass
        logger.error(
            "Task failed task_id=%s task_type=%s job_id=%s application_id=%s error=%s",
            task_id,
            task_type,
            job_id,
            application_id,
            sanitized_error,
        )

    def _mark_failed(self, task_id: str, message: str) -> None:
        try:
            task = self.task_store.get(task_id)
            if task["state"] not in {"succeeded", "failed"}:
                self.task_store.transition(task_id, "failed", error=message)
        except TaskStoreError:
            return
