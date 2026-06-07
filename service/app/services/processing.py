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


DEFAULT_CANDIDATE_NAME = "Vincent Morrill"
DEFAULT_CANDIDATE_EMAIL = "vince.codefactory@outlook.com"


COVER_LETTER_SYSTEM_PROMPT = """You write concise, factual, role-targeted cover letters.

Return plain Markdown only.
Do not fabricate skills, achievements, dates, or employers.
Use only information present in the job payload and fit-analysis context.
If context is missing, write a neutral placeholder sentence rather than inventing details.
"""

CV_SYSTEM_PROMPT = """You write a targeted, copy-paste-ready CV draft in Markdown.

This is not a generic resume template and not a cover letter.
It must be tailored to one specific job and one specific candidate.

Hard constraints:
- Return plain Markdown only.
- Use only facts supported by the provided job, fit analysis, profile context, and document context.
- Never invent names, employers, titles, dates, degree names, certifications, projects, metrics, or contact details.
- Do not copy every detail from the profile context. Select only the strongest, most relevant evidence for this job.
- If a detail is missing or only partially verified, either omit it or phrase it conservatively.
- Do not use filler claims, generic placeholder companies, fake dates, or fake project names.
- Do not output commentary about your reasoning, source limitations, or what you chose to omit.

Content goals:
- Make the draft feel ready to refine and paste into a real CV workflow.
- Prioritize relevance, specificity, and truthfulness over completeness.
- Emphasize adjacent experience honestly when direct experience is limited.
- Keep the tone grounded, technical, and concise.
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


def read_optional_text_file(path: Path) -> str:
    try:
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def candidate_identity() -> dict[str, str]:
    return {
        "name": DEFAULT_CANDIDATE_NAME,
        "email": DEFAULT_CANDIDATE_EMAIL,
    }


def build_profile_context(settings: Settings) -> str:
    profile_dir = settings.app_data_dir / "profile"
    if not profile_dir.is_dir():
        return "Profile context unavailable."

    sections: list[str] = []
    for filename in (
        "base_profile.md",
        "resume_facts.md",
        "skills_inventory.md",
        "experience_bullets.md",
        "education.md",
        "voice_and_style.md",
        "job_preferences.md",
        "project_inventory.md",
        "disallowed_claims.md",
    ):
        content = read_optional_text_file(profile_dir / filename)
        if content:
            sections.append(f"## {filename}\n{content}")

    return "\n\n".join(sections) if sections else "Profile context unavailable."


def build_documents_context(settings: Settings) -> str:
    documents_dir = settings.app_data_dir / "documents"
    if not documents_dir.is_dir():
        return "Document context unavailable."

    sections: list[str] = []
    readme = read_optional_text_file(documents_dir / "README.md")
    if readme:
        sections.append(f"## documents/README.md\n{readme}")

    references_dir = documents_dir / "references"
    if references_dir.is_dir():
        reference_notes = []
        for path in sorted(references_dir.glob("*.txt")):
            content = read_optional_text_file(path)
            if content:
                reference_notes.append(f"### {path.name}\n{content}")
        if reference_notes:
            sections.append("## Reference Notes\n" + "\n\n".join(reference_notes))

    inventory_lines = []
    for folder_name in ("cv", "linkedin", "diplomas", "applications"):
        folder = documents_dir / folder_name
        if not folder.is_dir():
            continue
        file_names = [path.name for path in sorted(folder.iterdir()) if path.is_file() and path.name != ".gitkeep"]
        if file_names:
            inventory_lines.append(f"- {folder_name}: {', '.join(file_names)}")
    if inventory_lines:
        sections.append("## Document Inventory\n" + "\n".join(inventory_lines))

    return "\n\n".join(sections) if sections else "Document context unavailable."


def cv_title(job: dict[str, Any]) -> str:
    title = str(job.get("title") or "Unknown role").strip() or "Unknown role"
    company = str(job.get("company") or "Unknown company").strip() or "Unknown company"
    return f"CV Draft for {title} at {company}"


def format_cv_output(content: str, job: dict[str, Any], identity: dict[str, str]) -> str:
    title = cv_title(job)
    name = identity["name"]
    email = identity["email"]
    body = content.strip()

    body = body.replace("[Candidate Name]", name)
    body = body.replace("[Email]", email)

    lines = body.splitlines()
    while lines and (
        lines[0].strip().startswith("# ")
        or lines[0].strip() == name
        or lines[0].strip() == email
        or lines[0].strip().startswith("Candidate:")
        or lines[0].strip().startswith("Email:")
    ):
        lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)

    normalized_body = "\n".join(lines).strip()
    header = f"# {title}\n\n{name}\n{email}"
    return header + ("\n\n" + normalized_body if normalized_body else "") + "\n"


def generate_cv(application_id: str, settings: Settings) -> Path:
    if not is_safe_identifier(application_id):
        raise ProcessingError(f"invalid application id: {application_id}")

    app_dir = settings.app_data_dir / "applications" / application_id
    if not app_dir.is_dir():
        raise ProcessingError(f"application workspace not found: {application_id}")

    job = read_json_file(app_dir / "job.json", "job")
    fit = read_json_file(app_dir / "fit-analysis.json", "fit analysis")
    targeting = read_text_file(app_dir / "resume-targeting.md", "resume targeting notes")
    identity = candidate_identity()
    profile_context = build_profile_context(settings)
    documents_context = build_documents_context(settings)

    provider = OllamaProvider(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
    )
    request = ModelRequest(
        system_prompt=CV_SYSTEM_PROMPT,
        user_prompt=build_cv_prompt(
            job,
            fit,
            targeting,
            profile_context,
            documents_context,
            identity,
        ),
        temperature=0.3,
        max_tokens=2000,
        response_format="text",
    )

    try:
        response = provider.complete(request)
    except ModelProviderError as exc:
        raise ProcessingError(f"CV generation failed: {exc}") from exc

    content = response.text.strip()
    if not content:
        raise ProcessingError("CV generation returned empty content")

    output_path = app_dir / "cv-draft.md"
    output_path.write_text(format_cv_output(content, job, identity), encoding="utf-8")
    return output_path


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


def build_cv_prompt(
    job: dict[str, Any],
    fit: dict[str, Any],
    targeting: str,
    profile_context: str,
    documents_context: str,
    identity: dict[str, str],
) -> str:
    job_json = json.dumps(job, ensure_ascii=False, indent=2)
    fit_json = json.dumps(fit, ensure_ascii=False, indent=2)
    title = str(job.get("title") or "Unknown role")
    company = str(job.get("company") or "Unknown company")
    return f"""Write a tailored CV draft for this job.

Candidate identity to use exactly:
- Name: {identity['name']}
- Email: {identity['email']}

Output contract:
- The document title is handled outside the model. Do not add your own top-level title.
- Start the body with a short "## Professional Summary" section.
- Then include these sections in order when supported by the source facts:
  - ## Professional Summary
  - ## Selected Skills
  - ## Experience
  - ## Education
- Under Experience, use only verified employers or experience contexts from the provided profile material.
- Use concise bullets that are specific to this job at {company} for {title}.
- Prefer 2 to 4 bullets per experience section, only when they are strongly relevant.
- Omit weak or unsupported sections instead of inventing filler.
- Do not include a cover-letter greeting, sign-off, or narrative paragraphs that read like a cover letter.
- Do not use placeholders like [Candidate Name], [Email], [Phone], [Company], or fake employer names.
- Do not restate the entire source profile. Curate only the best evidence for this role.
- If exact dates, degree names, or titles are unavailable, omit them or keep wording conservative rather than fabricating details.

Writing rules:
- Optimize for copy-paste readiness.
- Sound grounded and practical, not inflated.
- Align bullets to the target role's stack, scope, and expectations.
- Use adjacent-experience language honestly when direct experience is limited.
- Respect all disallowed claims and claim boundaries in the provided profile context.

Job context JSON:
{job_json}

Fit analysis JSON:
{fit_json}

Resume targeting notes:
{targeting}

Curated profile context:
{profile_context}

Available document context:
{documents_context}
"""
