"""Adapter from the service API to the Phase 2 apply-from-file workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_job_search.apply_from_file import ApplyFromFileError, apply_from_file
from ai_job_search.model_provider import ModelRequest
from ai_job_search.model_provider import ModelProviderError
from ai_job_search.providers import OllamaProvider

from app.config import Settings
from app.services.job_store import is_safe_identifier


class ProcessingError(RuntimeError):
    """Raised when a job cannot be processed into an application workspace."""


COVER_LETTER_SYSTEM_PROMPT = """You write concise, factual, role-targeted cover letters.

Return plain Markdown only.
Do not fabricate skills, achievements, dates, or employers.
Use only information present in the job payload and fit-analysis context.
If context is missing, write a neutral placeholder sentence rather than inventing details.
"""


def process_job(job_path: Path, settings: Settings) -> Path:
    provider = OllamaProvider(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
    )
    try:
        return apply_from_file(
            job_path,
            provider=provider,
            repo_root=settings.app_data_dir,
        )
    except (ApplyFromFileError, ModelProviderError, OSError) as exc:
        raise ProcessingError(str(exc)) from exc


def generate_cover_letter(application_id: str, settings: Settings) -> Path:
    if not is_safe_identifier(application_id):
        raise ProcessingError(f"invalid application id: {application_id}")

    app_dir = settings.app_data_dir / "applications" / application_id
    if not app_dir.is_dir():
        raise ProcessingError(f"application workspace not found: {application_id}")

    job = read_json_file(app_dir / "job.json", "job")
    fit = read_json_file(app_dir / "fit-analysis.json", "fit analysis")
    notes = read_text_file(app_dir / "cover-letter-notes.md", "cover letter notes")

    provider = OllamaProvider(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
    )
    request = ModelRequest(
        system_prompt=COVER_LETTER_SYSTEM_PROMPT,
        user_prompt=build_cover_letter_prompt(job, fit, notes),
        temperature=0.3,
        max_tokens=1400,
        response_format="text",
    )

    try:
        response = provider.complete(request)
    except ModelProviderError as exc:
        raise ProcessingError(f"cover letter generation failed: {exc}") from exc

    content = response.text.strip()
    if not content:
        raise ProcessingError("cover letter generation returned empty content")

    output_path = app_dir / "cover-letter.md"
    output_path.write_text(content + "\n", encoding="utf-8")
    return output_path


def read_json_file(path: Path, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProcessingError(f"cannot read {label} from {path.name}") from exc
    if not isinstance(parsed, dict):
        raise ProcessingError(f"{label} in {path.name} must be a JSON object")
    return parsed


def read_text_file(path: Path, label: str) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ProcessingError(f"cannot read {label} from {path.name}") from exc
    if not text:
        raise ProcessingError(f"{label} in {path.name} is empty")
    return text


def build_cover_letter_prompt(job: dict[str, Any], fit: dict[str, Any], notes: str) -> str:
    job_json = json.dumps(job, ensure_ascii=False, indent=2)
    fit_json = json.dumps(fit, ensure_ascii=False, indent=2)
    return f"""Write a complete, job-specific cover letter in Markdown.

Output format:
- Start with a greeting line: "Dear Hiring Manager,"
- 3 to 5 short paragraphs
- End with a sign-off and candidate placeholder name: "Best regards,\n[Candidate Name]"

Style constraints:
- Professional and direct
- No tables
- No markdown code fences
- No invented claims

Job context JSON:
{job_json}

Fit analysis JSON:
{fit_json}

Cover letter notes:
{notes}
"""
