"""Validation helpers for captured job posting JSON files."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = [
    "id",
    "source",
    "source_url",
    "captured_at",
    "title",
    "company",
    "location",
    "remote_status",
    "employment_type",
    "seniority",
    "description_text",
    "requirements",
    "preferred_qualifications",
    "technologies",
    "responsibilities",
    "compensation",
    "application_status",
]

STRING_FIELDS = [
    "id",
    "source",
    "source_url",
    "captured_at",
    "title",
    "company",
    "location",
    "remote_status",
    "employment_type",
    "seniority",
    "description_text",
    "application_status",
]

OPTIONAL_STRING_FIELDS = [
    "raw_text",
    "capture_method",
    "notes",
]

LIST_FIELDS = [
    "requirements",
    "preferred_qualifications",
    "technologies",
    "responsibilities",
]

ENUMS = {
    "source": {"linkedin", "indeed", "ziprecruiter", "manual", "other"},
    "remote_status": {"remote", "hybrid", "onsite", "unknown"},
    "employment_type": {"full-time", "part-time", "contract", "internship", "unknown"},
    "seniority": {"intern", "junior", "mid", "senior", "unknown"},
    "application_status": {
        "captured",
        "scored",
        "maybe",
        "rejected",
        "drafting",
        "ready_to_apply",
        "applied",
        "interviewing",
        "closed",
    },
}


def load_json(path: Path) -> tuple[Any | None, list[str]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle), []
    except FileNotFoundError:
        return None, [f"file not found: {path}"]
    except json.JSONDecodeError as exc:
        return None, [f"invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}"]
    except OSError as exc:
        return None, [f"cannot read file: {exc}"]


def is_iso_datetime(value: str) -> bool:
    candidate = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return True


def validate_string_field(data: dict[str, Any], field: str, errors: list[str]) -> None:
    value = data.get(field)
    if not isinstance(value, str):
        errors.append(f"invalid type: {field} must be a string")
        return
    if field in {"id", "source_url", "captured_at", "title", "company", "description_text"} and not value.strip():
        errors.append(f"invalid value: {field} must not be empty")


def validate_string_list(data: dict[str, Any], field: str, errors: list[str]) -> None:
    value = data.get(field)
    if not isinstance(value, list):
        errors.append(f"invalid type: {field} must be a list")
        return
    for index, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"invalid type: {field}[{index}] must be a string")


def validate_compensation(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("invalid type: compensation must be an object")
        return

    for field in ("min", "max"):
        amount = value.get(field)
        if amount is not None and not isinstance(amount, (int, float)):
            errors.append(f"invalid type: compensation.{field} must be a number or null")

    for field in ("currency", "raw"):
        if field in value and not isinstance(value[field], str):
            errors.append(f"invalid type: compensation.{field} must be a string")


def validate_job(data: Any) -> list[str]:
    errors: list[str] = []

    if not isinstance(data, dict):
        return ["invalid type: root value must be an object"]

    for field in REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"missing field: {field}")

    for field in STRING_FIELDS:
        if field in data:
            validate_string_field(data, field, errors)

    for field in OPTIONAL_STRING_FIELDS:
        if field in data and data[field] is not None and not isinstance(data[field], str):
            errors.append(f"invalid type: {field} must be a string")

    for field in LIST_FIELDS:
        if field in data:
            validate_string_list(data, field, errors)

    for field, allowed_values in ENUMS.items():
        if field in data and isinstance(data[field], str) and data[field] not in allowed_values:
            allowed = ", ".join(sorted(allowed_values))
            errors.append(f"invalid enum: {field}={data[field]!r} must be one of: {allowed}")

    if "captured_at" in data and isinstance(data["captured_at"], str) and not is_iso_datetime(data["captured_at"]):
        errors.append("invalid datetime: captured_at must be an ISO-8601 string")

    if "compensation" in data:
        validate_compensation(data["compensation"], errors)

    if "fit_analysis" in data and data["fit_analysis"] is not None and not isinstance(data["fit_analysis"], dict):
        errors.append("invalid type: fit_analysis must be an object or null")

    return errors

