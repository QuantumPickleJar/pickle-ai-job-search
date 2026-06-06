"""Local model fit scoring for captured job postings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_job_search.job_validation import load_json, validate_job
from ai_job_search.model_provider import ModelProvider, ModelRequest


MINIMAL_PROFILE_CONTEXT = """# Minimal Placeholder Candidate Profile

Use this only because profile/resume_facts.md does not exist yet.

- Early-career software developer.
- Skills and interests: C#, .NET, ASP.NET, .NET Core, SQL, Angular, TypeScript, Docker, CI/CD exposure, Jira, Confluence.
- Experience direction: backend/API development, debugging, technical documentation, university IT experience.
- Safe specific experience claim: Applied Benefits junior developer/internship experience.
- Do not invent employer-specific achievements, senior-level ownership, production cloud ownership, or leadership claims beyond this placeholder.
"""

SYSTEM_PROMPT = """You are a conservative job fit scoring assistant.

Return JSON only. Do not include Markdown, commentary, or extra keys that are not useful.

Evaluate the captured job against the candidate profile. Avoid unsupported claims.
Identify missing skills honestly. Prefer "maybe" over inflated "apply" when uncertain.
Recommend "skip" for senior-only or clearly mismatched roles.

Required JSON shape:
{
  "overall_score": 0,
  "recommendation": "apply | maybe | skip",
  "reasons_to_apply": [],
  "risks": [],
  "matched_skills": [],
  "missing_skills": [],
  "resume_keywords_to_include": [],
  "suggested_resume_angle": "",
  "cover_letter_angle": "",
  "questions_to_answer_before_applying": []
}

Scoring rubric:
- 90-100: strong apply, direct match with .NET/C#/SQL/backend/API/Angular/Docker or related stack.
- 75-89: apply, good match with minor gaps.
- 60-74: maybe, useful fit but uncertain or with meaningful gaps.
- Below 60: skip unless there is a specific strategic reason.
"""

REQUIRED_ANALYSIS_FIELDS = [
    "overall_score",
    "recommendation",
    "reasons_to_apply",
    "risks",
    "matched_skills",
    "missing_skills",
    "resume_keywords_to_include",
    "suggested_resume_angle",
    "cover_letter_angle",
    "questions_to_answer_before_applying",
]

LIST_ANALYSIS_FIELDS = [
    "reasons_to_apply",
    "risks",
    "matched_skills",
    "missing_skills",
    "resume_keywords_to_include",
    "questions_to_answer_before_applying",
]

OPTIONAL_LIST_ANALYSIS_FIELDS = ["do_not_claim"]
STRING_ANALYSIS_FIELDS = ["suggested_resume_angle", "cover_letter_angle"]
OPTIONAL_STRING_ANALYSIS_FIELDS = ["confidence"]
RECOMMENDATIONS = {"apply", "maybe", "skip"}
CONFIDENCE_VALUES = {"low", "medium", "high"}
PROFILE_CONTEXT_FILES = [
    "resume_facts.md",
    "disallowed_claims.md",
]


class FitScoringError(RuntimeError):
    """Raised when fit scoring cannot produce valid output."""


def load_profile_context(repo_root: Path) -> str:
    sections = []
    for filename in PROFILE_CONTEXT_FILES:
        path = repo_root / "profile" / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").strip()
        if text:
            sections.append(f"## Source: profile/{filename}\n\n{text}")
    if sections:
        return "\n\n".join(sections)
    return MINIMAL_PROFILE_CONTEXT


def output_dir_for_job(job_path: Path, repo_root: Path) -> Path:
    if job_path.parent.name == "examples":
        return repo_root / "applications" / "examples" / job_path.stem
    return repo_root / "applications" / job_path.stem


def build_user_prompt(job: dict[str, Any], profile_context: str) -> str:
    job_json = json.dumps(job, ensure_ascii=False, indent=2)
    return f"""Candidate profile context:
{profile_context}

Captured job JSON:
{job_json}

Score this job now. Return only the required JSON object.
"""


def parse_analysis(text: str) -> Any:
    return json.loads(text)


def validate_fit_analysis(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["root value must be an object"]

    for field in REQUIRED_ANALYSIS_FIELDS:
        if field not in data:
            errors.append(f"missing field: {field}")

    score = data.get("overall_score")
    if not isinstance(score, (int, float)):
        errors.append("invalid type: overall_score must be a number")
    elif score < 0 or score > 100:
        errors.append("invalid value: overall_score must be between 0 and 100")

    recommendation = data.get("recommendation")
    if not isinstance(recommendation, str):
        errors.append("invalid type: recommendation must be a string")
    elif recommendation not in RECOMMENDATIONS:
        errors.append("invalid enum: recommendation must be one of: apply, maybe, skip")

    for field in LIST_ANALYSIS_FIELDS:
        value = data.get(field)
        if not isinstance(value, list):
            errors.append(f"invalid type: {field} must be a list")
            continue
        for index, item in enumerate(value):
            if not isinstance(item, str):
                errors.append(f"invalid type: {field}[{index}] must be a string")

    for field in OPTIONAL_LIST_ANALYSIS_FIELDS:
        if field in data:
            value = data[field]
            if not isinstance(value, list):
                errors.append(f"invalid type: {field} must be a list")
                continue
            for index, item in enumerate(value):
                if not isinstance(item, str):
                    errors.append(f"invalid type: {field}[{index}] must be a string")

    for field in STRING_ANALYSIS_FIELDS:
        if not isinstance(data.get(field), str):
            errors.append(f"invalid type: {field} must be a string")

    for field in OPTIONAL_STRING_ANALYSIS_FIELDS:
        if field in data and not isinstance(data[field], str):
            errors.append(f"invalid type: {field} must be a string")

    if "confidence" in data and isinstance(data["confidence"], str) and data["confidence"] not in CONFIDENCE_VALUES:
        errors.append("invalid enum: confidence must be one of: low, medium, high")

    return errors


def normalize_fit_analysis(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    score = normalized.get("overall_score")
    if isinstance(score, float) and score.is_integer():
        normalized["overall_score"] = int(score)
    return normalized


def score_job_file(
    job_path: Path,
    provider: ModelProvider,
    repo_root: Path,
    output_dir: Path | None = None,
) -> Path:
    job, load_errors = load_json(job_path)
    if load_errors:
        raise FitScoringError("; ".join(load_errors))

    job_errors = validate_job(job)
    if job_errors:
        raise FitScoringError("job validation failed: " + "; ".join(job_errors))

    profile_context = load_profile_context(repo_root)
    request = ModelRequest(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=build_user_prompt(job, profile_context),
        temperature=0,
        max_tokens=1200,
        response_format="json",
    )
    response = provider.complete(request)
    output_dir = output_dir or output_dir_for_job(job_path, repo_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "fit-analysis.raw.txt"

    try:
        parsed = parse_analysis(response.text)
    except json.JSONDecodeError as exc:
        raw_path.write_text(response.text, encoding="utf-8")
        raise FitScoringError(f"model returned malformed JSON; raw response saved to {raw_path}: {exc}") from exc

    analysis_errors = validate_fit_analysis(parsed)
    if analysis_errors:
        raw_path.write_text(response.text, encoding="utf-8")
        raise FitScoringError(
            "model returned invalid fit-analysis shape; raw response saved to "
            f"{raw_path}: " + "; ".join(analysis_errors)
        )

    output_path = output_dir / "fit-analysis.json"
    output_path.write_text(
        json.dumps(normalize_fit_analysis(parsed), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path
