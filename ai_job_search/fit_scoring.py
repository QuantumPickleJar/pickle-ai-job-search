"""Local model fit scoring for captured job postings."""

from __future__ import annotations

import json
import os
import re
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


def _read_positive_int_env(name: str, default: int, minimum: int = 1, maximum: int = 50) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except ValueError:
        return default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


FIT_ANALYSIS_MAX_MODEL_CALLS = _read_positive_int_env("FIT_ANALYSIS_MAX_MODEL_CALLS", default=3, minimum=1, maximum=10)
FIT_ANALYSIS_REPAIR_PASSES = _read_positive_int_env("FIT_ANALYSIS_REPAIR_PASSES", default=1, minimum=0, maximum=5)
FIT_ANALYSIS_MAX_PROFILE_CHARS = _read_positive_int_env(
    "FIT_ANALYSIS_MAX_PROFILE_CHARS",
    default=12000,
    minimum=2000,
    maximum=50000,
)

SYSTEM_PROMPT = """You are a conservative job fit scoring assistant.

Return JSON only. Do not include Markdown, prose, commentary, wrappers, or alternative key names.
The top-level JSON object must contain the required keys directly.
Do not nest under analysis, fit_analysis, result, assessment, job_fit, or similar keys.

Evaluate the captured job against the candidate profile. Avoid unsupported claims.
Identify missing skills honestly. Prefer "maybe" over inflated "apply" when uncertain.
Recommend "skip" for senior-only or clearly mismatched roles.

Hard factual constraints:
- The candidate has professional software/application development experience.
- Do not claim the candidate lacks 2-3 years of general software development, software engineering, or application development experience.
- The candidate has enterprise application experience through Secura/BizLink, UWO Portal/RoStar, and Applied Systems/Applied Benefits.
- If a posting requires senior enterprise architecture ownership, that may be a gap; do not convert that into "no enterprise experience."
- Missing skills belong in internal risk notes, not self-disqualifying cover-letter prose.
- Do not mention certifications unless a certification is explicitly present in profile facts.

CRITICAL CONSTRAINT - Enterprise Experience & Ownership:
- The candidate HAS professional enterprise application experience (BizLink insurance quoting, UWO Portal, Dynamic Plan Benefits Designer).
- Do NOT claim the candidate lacks enterprise application experience.
- If a posting requires SENIOR ENTERPRISE ARCHITECTURE OWNERSHIP, frame that as a specific seniority/ownership gap, NOT as lack of enterprise exposure.
- Do NOT claim the candidate owns or owned: BizLink, AgencyPortal, PowerWriter, ImageRight, UWO Portal, or the Applied Benefits platform architecture.
- Describe contributions accurately: "contributed to", "worked on", "implemented features in", not "owned" or "architected".

Required JSON shape exactly:
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

Valid tiny example:
{"overall_score":72,"recommendation":"maybe","reasons_to_apply":["Direct C# and .NET overlap."],"risks":["Some tooling requirements are unclear."],"matched_skills":["C#",".NET"],"missing_skills":["Azure"],"resume_keywords_to_include":["ASP.NET Core"],"suggested_resume_angle":"Emphasize backend application development outcomes.","cover_letter_angle":"Position as a practical .NET contributor with enterprise experience.","questions_to_answer_before_applying":["Is Azure mandatory for day one?"]}

Recommendation must be one of: apply, maybe, skip.
overall_score must be a number from 0 to 100.

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
    "base_profile.md",
    "resume_facts.md",
    "education.md",
    "skills_inventory.md",
    "experience_bullets.md",
    "project_inventory.md",
    "experience_timeline.md",
    "job_preferences.md",
    "voice_and_style.md",
    "disallowed_claims.md",
    "generation-constraints.md",
    "cover_letter_evidence.md",
]

FIT_PROFILE_CONTEXT_FILES = [
    "base_profile.md",
    "resume_facts.md",
    "skills_inventory.md",
    "experience_timeline.md",
    "cover_letter_evidence.md",
    "disallowed_claims.md",
]

FIT_ANALYSIS_WRAPPER_KEYS = ["fit_analysis", "analysis", "result", "job_fit", "assessment"]
FIT_ANALYSIS_ALIAS_MAP = {
    "score": "overall_score",
    "fit_score": "overall_score",
    "overall": "overall_score",
    "decision": "recommendation",
    "apply_recommendation": "recommendation",
    "strengths": "reasons_to_apply",
    "why_apply": "reasons_to_apply",
    "concerns": "risks",
    "gaps": "missing_skills",
    "skills_matched": "matched_skills",
    "keywords": "resume_keywords_to_include",
    "resume_keywords": "resume_keywords_to_include",
    "resume_angle": "suggested_resume_angle",
    "letter_angle": "cover_letter_angle",
    "questions": "questions_to_answer_before_applying",
}

FIT_OUTPUT_SAFETY_FIELDS = [
    "risks",
    "missing_skills",
    "cover_letter_angle",
    "questions_to_answer_before_applying",
]

DISQUALIFYING_PHRASES = [
    "i do not meet",
    "i don't meet",
    "i lack the minimum",
    "lacks the minimum 2-3 years",
    "lacks 2-3 years",
    "does not meet the minimum",
    "no enterprise experience",
    "limited exposure to enterprise",
    "[your name]",
    "[your address]",
    "[mention",
]


class FitScoringError(RuntimeError):
    """Raised when fit scoring cannot produce valid output."""


def empty_fit_analysis() -> dict[str, Any]:
    return {
        "overall_score": 0,
        "recommendation": "maybe",
        "reasons_to_apply": [],
        "risks": [],
        "matched_skills": [],
        "missing_skills": [],
        "resume_keywords_to_include": [],
        "suggested_resume_angle": "",
        "cover_letter_angle": "",
        "questions_to_answer_before_applying": [],
    }


def profile_context_status(repo_root: Path) -> dict[str, Any]:
    app_data_dir = os.getenv("APP_DATA_DIR", "").strip()
    candidate_roots: list[tuple[str, Path]] = [("repo_root", repo_root / "profile")]
    if app_data_dir:
        app_data_profile = Path(app_data_dir) / "profile"
        if app_data_profile not in [path for _, path in candidate_roots]:
            candidate_roots.append(("APP_DATA_DIR", app_data_profile))

    selected_label = candidate_roots[0][0]
    selected_profile_dir = candidate_roots[0][1]
    for label, path in candidate_roots:
        if path.exists():
            selected_label = label
            selected_profile_dir = path
            break

    files: list[dict[str, Any]] = []
    loaded: list[str] = []
    missing: list[str] = []
    for filename in PROFILE_CONTEXT_FILES:
        file_path = selected_profile_dir / filename
        exists = file_path.exists()
        is_loaded = False
        chars = 0
        if exists:
            text = file_path.read_text(encoding="utf-8").strip()
            chars = len(text)
            is_loaded = bool(text)
        if is_loaded:
            loaded.append(filename)
        else:
            missing.append(filename)
        files.append(
            {
                "filename": filename,
                "path": str(file_path),
                "exists": exists,
                "loaded": is_loaded,
                "chars": chars,
            }
        )

    return {
        "repo_root": str(repo_root),
        "selected_profile_dir": str(selected_profile_dir),
        "selected_profile_source": selected_label,
        "candidate_profile_dirs": [{"source": label, "path": str(path)} for label, path in candidate_roots],
        "loaded_files": loaded,
        "missing_files": missing,
        "files": files,
    }


def load_profile_context(repo_root: Path) -> str:
    status = profile_context_status(repo_root)
    profile_dir = Path(status["selected_profile_dir"])
    sections = []
    for filename in PROFILE_CONTEXT_FILES:
        path = profile_dir / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").strip()
        if text:
            sections.append(f"## Source: profile/{filename}\n\n{text}")
    if sections:
        return "\n\n".join(sections)
    return MINIMAL_PROFILE_CONTEXT


def load_fit_profile_context(repo_root: Path, max_chars: int = FIT_ANALYSIS_MAX_PROFILE_CHARS) -> str:
    status = profile_context_status(repo_root)
    profile_dir = Path(status["selected_profile_dir"])

    sections: list[str] = []
    current_chars = 0
    for filename in FIT_PROFILE_CONTEXT_FILES:
        path = profile_dir / filename
        if not path.exists():
            continue

        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue

        section = f"## Source: profile/{filename}\n\n{text}"
        separator = "\n\n" if sections else ""
        candidate = f"{separator}{section}"
        remaining = max_chars - current_chars
        if remaining <= 0:
            break

        if len(candidate) <= remaining:
            sections.append(section)
            current_chars += len(candidate)
            continue

        if remaining > len(separator):
            allowed = remaining - len(separator)
            if allowed > 0:
                truncated = section[:allowed].rstrip()
                if truncated:
                    if len(truncated) < len(section):
                        truncated += "\n\n[truncated]"
                    sections.append(truncated)
        break

    if sections:
        joined = "\n\n".join(sections)
        if len(joined) > max_chars:
            return joined[:max_chars].rstrip()
        return joined
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


def _extract_fit_analysis_candidate_with_metadata(parsed: Any) -> tuple[dict[str, Any] | None, bool]:
    if not isinstance(parsed, dict):
        return None, False

    for key in FIT_ANALYSIS_WRAPPER_KEYS:
        value = parsed.get(key)
        if isinstance(value, dict):
            return dict(value), True

    if len(parsed) == 1:
        only_value = next(iter(parsed.values()))
        if isinstance(only_value, dict):
            return dict(only_value), True

    return dict(parsed), False


def extract_fit_analysis_candidate(parsed: Any) -> dict[str, Any] | None:
    candidate, _ = _extract_fit_analysis_candidate_with_metadata(parsed)
    return candidate


def _coerce_score(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if re.fullmatch(r"\d+(\.\d+)?", stripped):
            if "." in stripped:
                return float(stripped)
            return int(stripped)
    return value


def _normalize_recommendation_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    lowered = value.strip().lower()
    if lowered in RECOMMENDATIONS:
        return lowered

    obvious_map = {
        "strong apply": "apply",
        "apply now": "apply",
        "recommended apply": "apply",
        "yes": "apply",
        "consider": "maybe",
        "maybe apply": "maybe",
        "unsure": "maybe",
        "pass": "skip",
        "do not apply": "skip",
        "reject": "skip",
        "no": "skip",
    }
    if lowered in obvious_map:
        return obvious_map[lowered]
    if lowered.startswith("apply"):
        return "apply"
    if lowered.startswith("maybe"):
        return "maybe"
    if lowered.startswith("skip"):
        return "skip"
    return value


def _coerce_list_field(value: Any) -> Any:
    if isinstance(value, str):
        if "," in value:
            parts = [part.strip() for part in value.split(",")]
            return [part for part in parts if part]
        stripped = value.strip()
        return [stripped] if stripped else []

    if isinstance(value, (tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]

    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    result.append(stripped)
            elif item is not None:
                stripped = str(item).strip()
                if stripped:
                    result.append(stripped)
        return result

    return value


def _normalize_fit_analysis_shape_with_metadata(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    normalized = dict(data)
    aliases_normalized: list[str] = []

    for alias, canonical in FIT_ANALYSIS_ALIAS_MAP.items():
        if alias in normalized and canonical not in normalized:
            normalized[canonical] = normalized[alias]
            aliases_normalized.append(f"{alias}->{canonical}")

    if "overall_score" in normalized:
        normalized["overall_score"] = _coerce_score(normalized.get("overall_score"))

    if "recommendation" in normalized:
        normalized["recommendation"] = _normalize_recommendation_value(normalized.get("recommendation"))

    for field in LIST_ANALYSIS_FIELDS + OPTIONAL_LIST_ANALYSIS_FIELDS:
        if field in normalized:
            normalized[field] = _coerce_list_field(normalized[field])

    for field in STRING_ANALYSIS_FIELDS + OPTIONAL_STRING_ANALYSIS_FIELDS:
        if field in normalized and not isinstance(normalized[field], str) and normalized[field] is not None:
            normalized[field] = str(normalized[field])

    score = normalized.get("overall_score")
    if isinstance(score, float) and score.is_integer():
        normalized["overall_score"] = int(score)

    return normalized, aliases_normalized


def normalize_fit_analysis_shape(data: dict[str, Any]) -> dict[str, Any]:
    normalized, _ = _normalize_fit_analysis_shape_with_metadata(data)
    return normalized


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
    return normalize_fit_analysis_shape(data)


def build_fit_analysis_repair_prompt(raw_response: str, validation_errors: list[str]) -> str:
    errors_text = "\n".join(f"- {error}" for error in validation_errors) or "- unknown validation error"
    return f"""Convert the previous model response into the required fit-analysis JSON schema.

Rules:
- Return JSON only.
- Do not add Markdown.
- Do not invent facts.
- Preserve useful content from the previous response when possible.
- Fill missing list fields with empty arrays only when data is not present.
- recommendation must be one of: apply, maybe, skip.
- overall_score must be a number from 0 to 100.

Validation errors from previous response:
{errors_text}

Required JSON shape exactly:
{{
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
}}

Previous response to repair:
{raw_response}
"""


def deterministic_fallback_fit_analysis() -> dict[str, Any]:
    fallback = empty_fit_analysis()
    fallback.update(
        {
            "overall_score": 65,
            "recommendation": "maybe",
            "reasons_to_apply": [
                "The role appears related to C#, .NET, SQL, or application development."
            ],
            "risks": ["Model fit scoring failed; review the job manually before applying."],
            "suggested_resume_angle": (
                "Review the job posting manually and emphasize only verified C#/.NET, SQL, "
                "and application development experience."
            ),
            "cover_letter_angle": (
                "Use a conservative application-services angle and avoid claiming unverified technologies."
            ),
            "questions_to_answer_before_applying": [
                "Which required technologies are truly mandatory?",
                "What experience level is expected?",
            ],
        }
    )
    return fallback


def _supports_certification_claims(profile_context: str) -> bool:
    return bool(re.search(r"\b(certification|certifications|certified|certificate)\b", profile_context, re.IGNORECASE))


def _contains_blocked_phrase(text: str, supports_certifications: bool) -> list[str]:
    found: list[str] = []
    lowered = text.lower()
    for phrase in DISQUALIFYING_PHRASES:
        if phrase in lowered:
            found.append(phrase)
    if not supports_certifications and "certification" in lowered:
        found.append("certifications")
    return found


def _rewrite_disqualifying_text(text: str, supports_certifications: bool) -> str:
    lowered = text.lower()

    if "enterprise" in lowered and ("no enterprise experience" in lowered or "limited exposure to enterprise" in lowered):
        return (
            "Enterprise application experience is present. If required, describe the gap as limited senior "
            "architecture ownership rather than lack of enterprise experience."
        )

    if (
        "2-3 years" in lowered
        or "i do not meet" in lowered
        or "i don't meet" in lowered
        or "i lack the minimum" in lowered
        or "does not meet the minimum" in lowered
    ):
        return (
            "General software/application development experience is present. If there is a gap, name the "
            "specific technology or domain that is not yet verified."
        )

    cleaned = text
    cleaned = re.sub(r"\[Your Name\]", "candidate", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\[Your Address\]", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\[mention", "mention", cleaned, flags=re.IGNORECASE)
    if not supports_certifications and re.search(r"\bcertifications?\b", cleaned, flags=re.IGNORECASE):
        return "Only include certifications if they are explicitly confirmed in profile facts."

    cleaned = cleaned.strip()
    if cleaned:
        return cleaned
    return "Name only specific, verifiable gaps and avoid self-disqualifying language."


def sanitize_fit_analysis_safety(
    data: dict[str, Any],
    profile_context: str,
) -> tuple[dict[str, Any], list[str]]:
    sanitized = dict(data)
    warnings: list[str] = []
    supports_certifications = _supports_certification_claims(profile_context)

    for field in FIT_OUTPUT_SAFETY_FIELDS:
        value = sanitized.get(field)
        if isinstance(value, str):
            found = _contains_blocked_phrase(value, supports_certifications)
            if found:
                sanitized[field] = _rewrite_disqualifying_text(value, supports_certifications)
                warnings.append(f"{field}: blocked phrases rewritten ({', '.join(found)})")
        elif isinstance(value, list):
            rewritten: list[Any] = []
            for item in value:
                if isinstance(item, str):
                    found = _contains_blocked_phrase(item, supports_certifications)
                    if found:
                        item = _rewrite_disqualifying_text(item, supports_certifications)
                        warnings.append(f"{field}: blocked phrases rewritten ({', '.join(found)})")
                rewritten.append(item)
            sanitized[field] = rewritten

    for field in FIT_OUTPUT_SAFETY_FIELDS:
        value = sanitized.get(field)
        if isinstance(value, str):
            remaining = _contains_blocked_phrase(value, supports_certifications)
            if remaining:
                raise FitScoringError(
                    f"fit-analysis safety check failed in {field}: blocked phrases remain ({', '.join(remaining)})"
                )
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, str):
                    remaining = _contains_blocked_phrase(item, supports_certifications)
                    if remaining:
                        raise FitScoringError(
                            "fit-analysis safety check failed in "
                            f"{field}[{index}]: blocked phrases remain ({', '.join(remaining)})"
                        )

    return sanitized, warnings


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

    profile_context = load_fit_profile_context(repo_root, max_chars=FIT_ANALYSIS_MAX_PROFILE_CHARS)
    request = ModelRequest(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=build_user_prompt(job, profile_context),
        temperature=0,
        max_tokens=1200,
        response_format="json",
    )

    output_dir = output_dir or output_dir_for_job(job_path, repo_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "fit-analysis.raw.txt"

    source = "model-original"
    model_query_count_actual = 0
    repair_attempted = False
    repair_successful = False
    validation_errors_original: list[str] = []
    validation_errors_repair: list[str] = []
    normalized_from_wrapper = False
    aliases_normalized: list[str] = []
    fallback_reason: str | None = None
    raw_response_path: str | None = None

    response = provider.complete(request)
    model_query_count_actual += 1

    normalized: dict[str, Any] | None = None
    original_raw_text = response.text
    current_raw_text = response.text

    try:
        parsed = parse_analysis(response.text)
        candidate, normalized_from_wrapper = _extract_fit_analysis_candidate_with_metadata(parsed)
        if candidate is None:
            validation_errors_original = ["root value must be an object"]
        else:
            normalized_candidate, alias_hits = _normalize_fit_analysis_shape_with_metadata(candidate)
            aliases_normalized.extend(alias_hits)
            validation_errors_original = validate_fit_analysis(normalized_candidate)
            if not validation_errors_original:
                normalized = normalized_candidate
                if normalized_from_wrapper or alias_hits or normalized_candidate != candidate:
                    source = "model-normalized"
    except json.JSONDecodeError as exc:
        validation_errors_original = [f"malformed JSON: {exc}"]

    if validation_errors_original:
        raw_path.write_text(original_raw_text, encoding="utf-8")
        raw_response_path = str(raw_path)

        remaining_budget = max(0, FIT_ANALYSIS_MAX_MODEL_CALLS - model_query_count_actual)
        repair_budget = min(FIT_ANALYSIS_REPAIR_PASSES, remaining_budget)
        for _ in range(repair_budget):
            repair_attempted = True
            repair_prompt = build_fit_analysis_repair_prompt(current_raw_text, validation_errors_original)
            repair_request = ModelRequest(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=repair_prompt,
                temperature=0,
                max_tokens=1200,
                response_format="json",
            )
            repair_response = provider.complete(repair_request)
            model_query_count_actual += 1
            current_raw_text = repair_response.text

            try:
                parsed_repair = parse_analysis(repair_response.text)
                repair_candidate, wrapper_from_repair = _extract_fit_analysis_candidate_with_metadata(parsed_repair)
                if repair_candidate is None:
                    validation_errors_repair = ["root value must be an object"]
                    continue

                normalized_repair, alias_hits_repair = _normalize_fit_analysis_shape_with_metadata(repair_candidate)
                validation_errors_repair = validate_fit_analysis(normalized_repair)
                if validation_errors_repair:
                    continue

                normalized = normalized_repair
                normalized_from_wrapper = normalized_from_wrapper or wrapper_from_repair
                aliases_normalized.extend(alias_hits_repair)
                source = "model-repaired"
                repair_successful = True
                break
            except json.JSONDecodeError as exc:
                validation_errors_repair = [f"malformed JSON: {exc}"]

    if normalized is None:
        source = "deterministic-fallback"
        fallback_reason = "model returned invalid fit-analysis shape after normalization and repair"
        normalized = deterministic_fallback_fit_analysis()

    if aliases_normalized:
        aliases_normalized = sorted(set(aliases_normalized))

    normalized, safety_warnings = sanitize_fit_analysis_safety(normalized, profile_context)
    if safety_warnings:
        safety_path = output_dir / "fit-analysis.safety-warnings.txt"
        safety_path.write_text("\n".join(safety_warnings) + "\n", encoding="utf-8")

    output_path = output_dir / "fit-analysis.json"
    output_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    metadata_path = output_dir / "fit-analysis.meta.json"
    metadata_path.write_text(
        json.dumps(
            {
                "source": source,
                "model_query_count_actual": model_query_count_actual,
                "repair_attempted": repair_attempted,
                "repair_successful": repair_successful,
                "validation_errors_original": validation_errors_original,
                "validation_errors_repair": validation_errors_repair,
                "raw_response_path": raw_response_path,
                "normalized_from_wrapper": normalized_from_wrapper,
                "aliases_normalized": aliases_normalized,
                "fallback_reason": fallback_reason,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return output_path
