"""Filesystem-backed jobs and application workspace storage."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from ai_job_search.intake import (
    JobIntakeError,
    captured_job_filename,
    normalize_capture,
    validate_capture_payload,
)


APPLICATION_FILES = (
    "job.json",
    "fit-analysis.json",
    "resume-targeting.md",
    "cover-letter-notes.md",
    "application-checklist.md",
)
SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


class StoreError(RuntimeError):
    """Base error for mounted data access."""


class ResourceNotFoundError(StoreError):
    """Raised when a requested job or application does not exist."""


class InvalidStoredDataError(StoreError):
    """Raised when persisted JSON cannot be read safely."""


class JobStore:
    _write_lock = threading.Lock()

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.captured_dir = data_dir / "job_intake" / "captured_jobs"
        self.applications_dir = data_dir / "applications"

    def save_capture(self, payload: dict[str, Any]) -> tuple[dict[str, Any], Path]:
        job = normalize_capture(validate_capture_payload(payload))
        job["id"] = normalize_identifier(str(job["id"]))

        with self._write_lock:
            self.captured_dir.mkdir(parents=True, exist_ok=True)
            job["id"] = self._unique_job_id(str(job["id"]))
            path = self._unique_job_path(captured_job_filename(job))
            temporary_path = path.with_suffix(".json.tmp")
            temporary_path.write_text(
                json.dumps(job, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(path)

        return job, path

    def list_jobs(self) -> list[dict[str, Any]]:
        if not self.captured_dir.exists():
            return []
        jobs = [
            self._read_json(path)
            for path in self.captured_dir.glob("*.json")
            if path.is_file() and not path.is_symlink()
        ]
        jobs.sort(key=lambda job: str(job.get("captured_at", "")), reverse=True)
        return [job_summary(job) for job in jobs]

    def get_job(self, job_id: str) -> tuple[dict[str, Any], Path]:
        for path in self._job_paths():
            job = self._read_json(path)
            if job.get("id") == job_id:
                return job, path
        raise ResourceNotFoundError(f"job not found: {job_id}")

    def list_applications(self) -> list[dict[str, Any]]:
        if not self.applications_dir.exists():
            return []

        applications = []
        for path in self.applications_dir.iterdir():
            if path.is_symlink() or not path.is_dir() or not is_safe_identifier(path.name):
                continue
            applications.append(application_summary(path))

        applications.sort(key=lambda item: item["application_id"])
        return applications

    def get_application(self, application_id: str) -> dict[str, Any]:
        if not is_safe_identifier(application_id):
            raise ResourceNotFoundError(f"application not found: {application_id}")

        path = self.applications_dir / application_id
        if path.is_symlink() or not path.is_dir():
            raise ResourceNotFoundError(f"application not found: {application_id}")

        files: dict[str, Any] = {}
        for filename in APPLICATION_FILES:
            file_path = path / filename
            if file_path.is_symlink() or not file_path.is_file():
                continue
            if file_path.suffix == ".json":
                files[filename] = self._read_json(file_path)
            else:
                try:
                    files[filename] = file_path.read_text(encoding="utf-8")
                except OSError as exc:
                    raise InvalidStoredDataError(f"cannot read application file: {filename}") from exc

        return {
            "application_id": application_id,
            "files": files,
        }

    def relative_path(self, path: Path) -> str:
        return path.relative_to(self.data_dir).as_posix()

    def _job_paths(self) -> list[Path]:
        if not self.captured_dir.exists():
            return []
        return sorted(
            path
            for path in self.captured_dir.glob("*.json")
            if path.is_file() and not path.is_symlink()
        )

    def _unique_job_id(self, base_id: str) -> str:
        existing = {
            str(self._read_json(path).get("id", ""))
            for path in self._job_paths()
        }
        if base_id not in existing:
            return base_id
        counter = 2
        while f"{base_id}-{counter}" in existing:
            counter += 1
        return f"{base_id}-{counter}"

    def _unique_job_path(self, filename: str) -> Path:
        path = self.captured_dir / filename
        counter = 2
        while path.exists():
            path = self.captured_dir / f"{Path(filename).stem}-{counter}.json"
            counter += 1
        return path

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidStoredDataError(f"cannot read stored JSON: {path.name}") from exc
        if not isinstance(data, dict):
            raise InvalidStoredDataError(f"stored JSON must be an object: {path.name}")
        return data


def is_safe_identifier(value: str) -> bool:
    return bool(SAFE_IDENTIFIER.fullmatch(value)) and ".." not in value


def normalize_identifier(value: str) -> str:
    identifier = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip(".-_")
    if not identifier or ".." in identifier:
        return "job"
    return identifier


def job_summary(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job.get("id"),
        "captured_at": job.get("captured_at"),
        "title": job.get("title"),
        "company": job.get("company"),
        "location": job.get("location"),
        "source": job.get("source"),
        "application_status": job.get("application_status"),
    }


def application_summary(path: Path) -> dict[str, Any]:
    available_files = [
        filename
        for filename in APPLICATION_FILES
        if (path / filename).is_file()
    ]
    return {
        "application_id": path.name,
        "available_files": available_files,
    }
