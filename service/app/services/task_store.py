"""Atomic JSON-backed task persistence."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TASK_STATES = {"queued", "running", "succeeded", "failed"}
TERMINAL_STATES = {"succeeded", "failed"}


class TaskStoreError(RuntimeError):
    """Base error for task persistence."""


class TaskNotFoundError(TaskStoreError):
    """Raised when a task identifier does not exist."""


class InvalidTaskDataError(TaskStoreError):
    """Raised when a persisted task cannot be parsed."""


class TaskStore:
    _lock = threading.RLock()

    def __init__(self, data_dir: Path) -> None:
        self.tasks_dir = data_dir / "tasks"

    def create(self, job_id: str) -> dict[str, Any]:
        timestamp = utc_timestamp()
        task = {
            "task_id": uuid.uuid4().hex,
            "job_id": job_id,
            "state": "queued",
            "created_at": timestamp,
            "updated_at": timestamp,
            "started_at": None,
            "completed_at": None,
            "application_id": None,
            "error": None,
        }
        with self._lock:
            self.tasks_dir.mkdir(parents=True, exist_ok=True)
            self._write(task)
        return task

    def get(self, task_id: str) -> dict[str, Any]:
        if not is_task_id(task_id):
            raise TaskNotFoundError(f"task not found: {task_id}")
        path = self.tasks_dir / f"{task_id}.json"
        if path.is_symlink() or not path.is_file():
            raise TaskNotFoundError(f"task not found: {task_id}")
        return self._read(path)

    def list(self) -> list[dict[str, Any]]:
        if not self.tasks_dir.exists():
            return []
        tasks = [
            self._read(path)
            for path in self.tasks_dir.glob("*.json")
            if path.is_file() and not path.is_symlink()
        ]
        tasks.sort(key=lambda task: str(task["created_at"]), reverse=True)
        return tasks

    def transition(
        self,
        task_id: str,
        state: str,
        *,
        application_id: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if state not in TASK_STATES:
            raise TaskStoreError(f"invalid task state: {state}")

        with self._lock:
            task = self.get(task_id)
            current_state = str(task["state"])
            allowed = {
                "queued": {"running", "failed"},
                "running": {"succeeded", "failed"},
                "succeeded": set(),
                "failed": set(),
            }
            if state not in allowed[current_state]:
                raise TaskStoreError(f"invalid task transition: {current_state} -> {state}")

            timestamp = utc_timestamp()
            task["state"] = state
            task["updated_at"] = timestamp
            if state == "running":
                task["started_at"] = timestamp
            if state in TERMINAL_STATES:
                task["completed_at"] = timestamp
                task["application_id"] = application_id
                task["error"] = error
            self._write(task)
            return task

    def recover(self) -> list[dict[str, Any]]:
        queued = []
        for task in self.list():
            if task["state"] == "queued":
                queued.append(task)
            elif task["state"] == "running":
                self.transition(
                    str(task["task_id"]),
                    "failed",
                    error="Task was interrupted by a service restart",
                )
        return queued

    def _write(self, task: dict[str, Any]) -> None:
        validate_task(task)
        path = self.tasks_dir / f"{task['task_id']}.json"
        temporary_path = path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(task, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidTaskDataError(f"cannot read task: {path.name}") from exc
        validate_task(task)
        return task


def is_task_id(value: str) -> bool:
    return len(value) == 32 and all(character in "0123456789abcdef" for character in value)


def validate_task(task: Any) -> None:
    if not isinstance(task, dict):
        raise InvalidTaskDataError("task JSON must be an object")
    required = {"task_id", "job_id", "state", "created_at", "updated_at"}
    missing = sorted(required - task.keys())
    if missing:
        raise InvalidTaskDataError("task is missing field(s): " + ", ".join(missing))
    if not is_task_id(str(task["task_id"])):
        raise InvalidTaskDataError("task_id is invalid")
    if not isinstance(task["job_id"], str) or not task["job_id"]:
        raise InvalidTaskDataError("job_id is invalid")
    if task["state"] not in TASK_STATES:
        raise InvalidTaskDataError("task state is invalid")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
