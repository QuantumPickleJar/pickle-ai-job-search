"""Normalize and save locally captured job postings."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_job_search.job_validation import validate_job


REQUIRED_CAPTURE_FIELDS = ["source", "source_url", "title", "company", "description_text"]
KNOWN_SOURCES = {"linkedin", "indeed", "ziprecruiter", "manual", "other"}
KNOWN_REMOTE_STATUS = {"remote", "hybrid", "onsite", "unknown"}
KNOWN_EMPLOYMENT_TYPES = {"full-time", "part-time", "contract", "internship", "unknown"}
KNOWN_SENIORITY = {"intern", "junior", "mid", "senior", "unknown"}


class JobIntakeError(RuntimeError):
    """Raised when a captured job cannot be normalized or saved."""


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower())
    slug = slug.strip("-")
    return slug or "unknown"


def safe_string(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return default
    return str(value).strip()


def normalize_enum(value: Any, allowed: set[str], default: str = "unknown") -> str:
    text = safe_string(value, default).lower()
    return text if text in allowed else default


def normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def normalize_compensation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"min": None, "max": None, "currency": "USD", "raw": ""}

    return {
        "min": value.get("min") if isinstance(value.get("min"), (int, float)) else None,
        "max": value.get("max") if isinstance(value.get("max"), (int, float)) else None,
        "currency": safe_string(value.get("currency"), "USD") or "USD",
        "raw": safe_string(value.get("raw"), ""),
    }


def validate_capture_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise JobIntakeError("request body must be a JSON object")

    missing = []
    for field in REQUIRED_CAPTURE_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            missing.append(field)

    if missing:
        raise JobIntakeError("missing required field(s): " + ", ".join(missing))

    return payload


def normalize_capture(payload: dict[str, Any]) -> dict[str, Any]:
    captured_at = safe_string(payload.get("captured_at")) or utc_timestamp()
    company = safe_string(payload.get("company"))
    title = safe_string(payload.get("title"))
    date_prefix = captured_at[:10] if len(captured_at) >= 10 else utc_timestamp()[:10]
    job_id = safe_string(payload.get("id")) or f"{date_prefix}_{slugify(company)}_{slugify(title)}"

    normalized = {
        "id": job_id,
        "source": normalize_enum(payload.get("source"), KNOWN_SOURCES, "other"),
        "source_url": safe_string(payload.get("source_url")),
        "captured_at": captured_at,
        "title": title,
        "company": company,
        "location": safe_string(payload.get("location"), "unknown") or "unknown",
        "remote_status": normalize_enum(payload.get("remote_status") or payload.get("workplace_type"), KNOWN_REMOTE_STATUS),
        "employment_type": normalize_enum(payload.get("employment_type"), KNOWN_EMPLOYMENT_TYPES),
        "seniority": normalize_enum(payload.get("seniority"), KNOWN_SENIORITY),
        "description_text": safe_string(payload.get("description_text")),
        "raw_text": safe_string(payload.get("raw_text")),
        "requirements": normalize_string_list(payload.get("requirements")),
        "preferred_qualifications": normalize_string_list(payload.get("preferred_qualifications")),
        "technologies": normalize_string_list(payload.get("technologies")),
        "responsibilities": normalize_string_list(payload.get("responsibilities")),
        "compensation": normalize_compensation(payload.get("compensation")),
        "fit_analysis": payload.get("fit_analysis") if isinstance(payload.get("fit_analysis"), dict) else None,
        "application_status": "captured",
        "capture_method": safe_string(payload.get("capture_method"), "manual_capture") or "manual_capture",
        "notes": safe_string(payload.get("notes")),
    }

    errors = validate_job(normalized)
    if errors:
        raise JobIntakeError("normalized job failed validation: " + "; ".join(errors))

    return normalized


def captured_job_filename(job: dict[str, Any]) -> str:
    captured_at = safe_string(job.get("captured_at"))
    timestamp = re.sub(r"[^0-9]", "", captured_at)[:14]
    if len(timestamp) < 14:
        timestamp = re.sub(r"[^0-9]", "", utc_timestamp())[:14]
    company = slugify(safe_string(job.get("company"), "unknown-company"))
    title = slugify(safe_string(job.get("title"), "unknown-role"))
    return f"{timestamp}-{company}-{title}.json"


def save_captured_job(payload: dict[str, Any], repo_root: Path) -> tuple[dict[str, Any], Path]:
    normalized = normalize_capture(validate_capture_payload(payload))
    captured_dir = repo_root / "job_intake" / "captured_jobs"
    captured_dir.mkdir(parents=True, exist_ok=True)

    path = captured_dir / captured_job_filename(normalized)
    counter = 2
    while path.exists():
        path = captured_dir / f"{path.stem}-{counter}{path.suffix}"
        counter += 1

    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized, path

