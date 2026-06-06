"""Create a reviewable application workspace from a captured job JSON file."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ai_job_search.fit_scoring import FitScoringError, score_job_file, validate_fit_analysis
from ai_job_search.job_validation import load_json, validate_job
from ai_job_search.model_provider import ModelProvider


class ApplyFromFileError(RuntimeError):
    """Raised when the apply-from-file workflow cannot complete."""


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower())
    slug = slug.strip("-")
    return slug or "unknown"


def application_slug(job: dict[str, Any]) -> str:
    company = slugify(str(job.get("company", "unknown-company")))
    title = slugify(str(job.get("title", "unknown-role")))
    return f"{company}-{title}"


def application_dir_for_job(job: dict[str, Any], repo_root: Path) -> Path:
    return repo_root / "applications" / application_slug(job)


def latest_captured_job(repo_root: Path) -> Path:
    captured_dir = repo_root / "job_intake" / "captured_jobs"
    candidates = [path for path in captured_dir.glob("*.json") if path.is_file()]
    if not candidates:
        raise ApplyFromFileError(f"no JSON files found in {captured_dir}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_valid_job(job_path: Path) -> dict[str, Any]:
    job, load_errors = load_json(job_path)
    if load_errors:
        raise ApplyFromFileError("; ".join(load_errors))

    errors = validate_job(job)
    if errors:
        raise ApplyFromFileError("job validation failed: " + "; ".join(errors))

    if not isinstance(job, dict):
        raise ApplyFromFileError("job validation failed: root value must be an object")
    return job


def load_valid_fit_analysis(path: Path) -> dict[str, Any]:
    analysis, load_errors = load_json(path)
    if load_errors:
        raise ApplyFromFileError("; ".join(load_errors))

    errors = validate_fit_analysis(analysis)
    if errors:
        raise ApplyFromFileError("fit analysis validation failed: " + "; ".join(errors))

    if not isinstance(analysis, dict):
        raise ApplyFromFileError("fit analysis validation failed: root value must be an object")
    return analysis


def ensure_fit_analysis(job_path: Path, app_dir: Path, provider: ModelProvider, repo_root: Path) -> dict[str, Any]:
    fit_path = app_dir / "fit-analysis.json"
    if fit_path.exists():
        return load_valid_fit_analysis(fit_path)

    try:
        score_job_file(job_path, provider=provider, repo_root=repo_root, output_dir=app_dir)
    except FitScoringError as exc:
        raise ApplyFromFileError(str(exc)) from exc

    return load_valid_fit_analysis(fit_path)


def format_list(items: Any, fallback: str = "None identified yet.") -> str:
    if not isinstance(items, list) or not items:
        return f"- {fallback}"
    lines = []
    for item in items:
        text = str(item).strip()
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines) if lines else f"- {fallback}"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_resume_targeting(path: Path, job: dict[str, Any], fit: dict[str, Any]) -> None:
    title = job.get("title", "Unknown role")
    company = job.get("company", "Unknown company")
    score = fit.get("overall_score", "unknown")
    recommendation = fit.get("recommendation", "unknown")
    angle = fit.get("suggested_resume_angle", "")

    content = f"""# Resume Targeting Notes

## Job

- Title: {title}
- Company: {company}
- Source: {job.get("source_url", "")}

## Fit Summary

- Overall score: {score}
- Recommendation: {recommendation}

## Skills To Emphasize

{format_list(fit.get("matched_skills"))}

## Missing Skills To Avoid Overclaiming

{format_list(fit.get("missing_skills"))}

## Suggested Resume Angle

{angle or "Review the fit analysis and write a grounded angle before creating a tailored resume."}

## Keywords To Consider

{format_list(fit.get("resume_keywords_to_include"))}

## Suggested Bullets To Consider

These are suggestions only. Do not paste them into a final resume until each claim is verified against profile facts.

{format_list(fit.get("reasons_to_apply"))}
"""
    path.write_text(content, encoding="utf-8")


def write_cover_letter_notes(path: Path, job: dict[str, Any], fit: dict[str, Any]) -> None:
    content = f"""# Cover Letter Notes

## Cover Letter Angle

{fit.get("cover_letter_angle") or "Review the fit analysis and write a grounded cover letter angle."}

## Company And Job Hooks

- Company: {job.get("company", "Unknown company")}
- Role: {job.get("title", "Unknown role")}
- Location: {job.get("location", "Unknown location")}
- Source: {job.get("source_url", "")}

## Candidate Strengths To Emphasize

{format_list(fit.get("matched_skills"))}

## Warnings About Claims Not To Make

{format_list(fit.get("missing_skills") or fit.get("do_not_claim"), "No warnings identified yet. Still verify every claim against profile facts.")}

## Questions Before Applying

{format_list(fit.get("questions_to_answer_before_applying"))}
"""
    path.write_text(content, encoding="utf-8")


def write_application_checklist(path: Path, job: dict[str, Any], fit: dict[str, Any]) -> None:
    content = f"""# Application Checklist

- [ ] Review job details for {job.get("company", "Unknown company")} - {job.get("title", "Unknown role")}
- [ ] Review fit score: {fit.get("overall_score", "unknown")} ({fit.get("recommendation", "unknown")})
- [ ] Review tailored resume notes in `resume-targeting.md`
- [ ] Review cover letter notes in `cover-letter-notes.md`
- [ ] Verify all claims against profile facts before drafting final documents
- [ ] Submit manually through the employer's application flow
- [ ] Record application status after submission

## Tracking

- Source URL: {job.get("source_url", "")}
- Resume version:
- Cover letter version:
- Submission status: not submitted
- Follow-up reminder:
"""
    path.write_text(content, encoding="utf-8")


def apply_from_file(job_path: Path, provider: ModelProvider, repo_root: Path) -> Path:
    job = load_valid_job(job_path)
    app_dir = application_dir_for_job(job, repo_root)
    fit = ensure_fit_analysis(job_path, app_dir=app_dir, provider=provider, repo_root=repo_root)

    app_dir.mkdir(parents=True, exist_ok=True)
    normalized_job_path = app_dir / "job.json"
    write_json(normalized_job_path, job)
    write_resume_targeting(app_dir / "resume-targeting.md", job, fit)
    write_cover_letter_notes(app_dir / "cover-letter-notes.md", job, fit)
    write_application_checklist(app_dir / "application-checklist.md", job, fit)

    generated_dir = app_dir / "generated"
    generated_dir.mkdir(exist_ok=True)
    keep = generated_dir / ".gitkeep"
    if not keep.exists():
        keep.write_text("", encoding="utf-8")

    return app_dir
