"""Adapter from the service API to the Phase 2 apply-from-file workflow."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_job_search.apply_from_file import ApplyFromFileError, apply_from_file
from ai_job_search.model_provider import ModelRequest
from ai_job_search.model_provider import ModelProvider
from ai_job_search.model_provider import ModelProviderError
from ai_job_search.providers import OllamaProvider

from app.config import Settings
from app.services.evidence_tools import ALLOWED_PROFILE_EVIDENCE_FILES
from app.services.evidence_tools import build_evidence_queries
from app.services.evidence_tools import is_dirty_cover_letter_text
from app.services.evidence_tools import list_profile_evidence_sources
from app.services.evidence_tools import plan_cover_letter_evidence_queries
from app.services.evidence_tools import search_profile_evidence
from app.services.job_store import is_safe_identifier


logger = logging.getLogger(__name__)


class ProcessingError(RuntimeError):
    """Raised when a job cannot be processed into an application workspace."""


@dataclass(frozen=True)
class CoverLetterGenerationResult:
    content: str
    source: str
    review_passes_enabled: bool
    model_query_count_expected: int
    model_query_count_actual: int
    evidence_query_count_actual: int
    dirty_evidence_rejected_count: int
    evidence_cards_used: list[dict[str, str]]
    evidence_cards_rejected_sample: list[str]
    tool_access: dict[str, Any]
    repair_attempted: bool = False
    repair_successful: bool = False
    fallback_reason: str | None = None
    validation_error: str | None = None


DEFAULT_CANDIDATE_NAME = "Vincent Morrill"
DEFAULT_CANDIDATE_EMAIL = "vince.codefactory@outlook.com"

COVER_LETTER_BLOCKED_PHRASES = [
    "[candidate",
    "[your",
    "[mention",
    "minimum 2-3 years",
    "in remote",
    "ideal candidate",
    "seamlessly integrate",
    "robust solutions",
    "as a language model",
    "based on the provided",
    "i am actively deepening",
    "actively deepening my experience with",
    "i do not meet",
    "i don't meet",
    "i lack",
    "lacks the minimum",
    "limited exposure",
    "todo",
    "tbd",
    "fixme",
    "relevant coursework or projects",
    "examples include relevant",
    "examples include todo",
    ": todo",
    "where supported by actual projects",
    "add verified",
    "project template",
    "known technical areas",
    "candidate project leads",
    "manual review notes",
    "missing details to fill manually",
    "safe themes to verify",
    "technical skills to verify",
    "verify and expand",
    "unless verified",
    "claims to avoid",
    "do not claim",
    "sql or relational database work",
    "c#, .net, asp.net, or .net core work",
    "and sql and",
]


COVER_LETTER_DRAFT_SYSTEM_PROMPT = """You write polished, factual, role-targeted cover letters.

Write plain text only.
Do not include headings or code fences.
Use only information from the provided Letter Brief.
Never include internal evaluation language, requirement copy, or self-disqualifying statements.
Use 1 to 2 concrete evidence cards from evidence_cards and weave them naturally into prose.
Do not list every evidence card.
Avoid generic filler and avoid repeating broad phrases like enterprise application development without concrete evidence.
"""

COVER_LETTER_CRITIQUE_SYSTEM_PROMPT = """You are a strict cover letter editor.

Return concise critique bullets only.
Flag issues against the rubric and suggest concrete edits.
"""

COVER_LETTER_FINAL_SYSTEM_PROMPT = """You finalize cover letters for submission readiness.

Write plain text only.
Use the Letter Brief, draft, and critique.
Do not include headings, JSON, or code fences.
Use 1 to 2 concrete evidence cards from evidence_cards and weave them naturally into prose.
Do not list every evidence card.
Avoid generic filler and avoid repeating broad phrases like enterprise application development without concrete evidence.
"""


COVER_LETTER_SINGLE_PASS_SYSTEM_PROMPT = """You write concise, factual, role-targeted cover letters.

Return plain Markdown only.
Do not fabricate skills, achievements, dates, or employers.
Use only information present in the provided Letter Brief.
If context is missing, write a neutral placeholder sentence rather than inventing details.

Critical claim constraints:
- Do not claim the candidate lacks enterprise application experience.
- The candidate has enterprise application contribution experience.
- If a posting requires senior enterprise architecture ownership, describe only that ownership level as a gap.
- Do not claim the candidate owned architecture for BizLink, AgencyPortal, PowerWriter, ImageRight, UWO Portal, or the Applied benefits platform.
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
- Do not use reference-letter names, referee employers, or unrelated document names as candidate employment history.
- Do not mention any employer or school unless it appears in the verified candidate context.
- Do not produce code fences, meta-instructions, or labels like "Tailored CV and Cover Letter".
- Do not output a cover letter, greeting, sign-off, references section, or explanatory notes.

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
        timeout_seconds=300,
    )
    try:
        return apply_from_file(
            job_path,
            provider=provider,
            repo_root=settings.app_data_dir,
        )
    except (ApplyFromFileError, ModelProviderError, OSError) as exc:
        raise ProcessingError(str(exc)) from exc


def generate_cover_letter(
    application_id: str,
    settings: Settings,
    query_callback: Any | None = None,
) -> Path:
    if not is_safe_identifier(application_id):
        raise ProcessingError(f"invalid application id: {application_id}")

    app_dir = settings.app_data_dir / "applications" / application_id
    if not app_dir.is_dir():
        raise ProcessingError(f"application workspace not found: {application_id}")

    job = read_json_file(app_dir / "job.json", "job")
    fit = read_json_file(app_dir / "fit-analysis.json", "fit analysis")
    profile_context = build_profile_context(settings)
    documents_context = build_documents_context(settings)
    identity = candidate_identity()

    provider = OllamaProvider(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        timeout_seconds=300,
    )

    try:
        result = generate_cover_letter_with_review(
            job=job,
            fit=fit,
            profile_context=profile_context,
            documents_context=documents_context,
            identity=identity,
            settings=settings,
            provider=provider,
            review_passes=settings.cover_letter_review_passes,
            query_callback=query_callback,
        )
    except ModelProviderError as exc:
        raise ProcessingError(f"cover letter generation failed: {exc}") from exc

    output_path = app_dir / "cover-letter.md"
    output_path.write_text(result.content + "\n", encoding="utf-8")
    meta = {
        "source": result.source,
        "review_passes_enabled": result.review_passes_enabled,
        "model_query_count_expected": result.model_query_count_expected,
        "model_query_count_actual": result.model_query_count_actual,
        "evidence_query_count_actual": result.evidence_query_count_actual,
        "dirty_evidence_rejected_count": result.dirty_evidence_rejected_count,
        "evidence_cards_used": result.evidence_cards_used,
        "evidence_cards_rejected_sample": result.evidence_cards_rejected_sample,
        "tool_access": result.tool_access,
        "repair_attempted": result.repair_attempted,
        "repair_successful": result.repair_successful,
        "fallback_reason": result.fallback_reason,
        "validation_error": result.validation_error,
    }
    (app_dir / "cover-letter.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if result.source == "deterministic-fallback" and result.fallback_reason:
        logger.warning("Cover letter fallback used application_id=%s reason=%s", application_id, result.fallback_reason)
    return output_path


def sanitize_generated_cover_letter(content: str) -> str:
    """Strip code fences and replace known safe placeholders with real candidate details."""
    text = content.strip()
    text = text.replace("```markdown", "").replace("```md", "").replace("```", "")
    # Case-insensitive replacement for name placeholders
    for placeholder in (
        "[Candidate Name]",
        "[candidate name]",
        "[Your Name]",
        "[your name]",
    ):
        text = text.replace(placeholder, DEFAULT_CANDIDATE_NAME)
    for placeholder in (
        "[Email]",
        "[Your Email]",
        "[your email]",
        "[email]",
    ):
        text = text.replace(placeholder, DEFAULT_CANDIDATE_EMAIL)
    return text.strip()


def validate_generated_cover_letter(content: str) -> None:
    lowered = content.lower()
    if is_dirty_cover_letter_text(content):
        raise ProcessingError("cover letter generation returned unsafe output. Dirty scaffold/template text detected.")
    for phrase in COVER_LETTER_BLOCKED_PHRASES:
        if phrase in lowered:
            raise ProcessingError(
                "cover letter generation returned unsafe output. "
                f"Blocked phrase found: {phrase}"
            )
    if "##" in content or "```" in content or re.search(r"(?m)^#\s", content):
        raise ProcessingError("cover letter generation returned unsafe output. Invalid markdown structure.")
    if not content.startswith("Dear Hiring Manager,"):
        raise ProcessingError("cover letter generation returned unsafe output. Missing required greeting.")
    if "Best regards," not in content:
        raise ProcessingError("cover letter generation returned unsafe output. Missing required sign-off.")
    if DEFAULT_CANDIDATE_NAME not in content:
        raise ProcessingError("cover letter generation returned unsafe output. Missing required candidate signature.")
    if re.search(r"\[[^\]]+\]", content):
        raise ProcessingError("cover letter generation returned unsafe output. Placeholder markers detected.")
    if any(marker in lowered for marker in ("{", "}", "\"role_title\"", "\"company\"")):
        raise ProcessingError("cover letter generation returned unsafe output. JSON-like content detected.")
    if any(marker in lowered for marker in ("[address", "street address", "city, state zip")):
        raise ProcessingError("cover letter generation returned unsafe output. Address placeholder detected.")
    grammar_fragments = ("and sql and", ". sql or", "projects. sql or")
    for fragment in grammar_fragments:
        if fragment in lowered:
            raise ProcessingError(
                "cover letter generation returned unsafe output. Grammar quality issue detected. "
                f"Fragment found: {fragment}"
            )
    if "and sql and enterprise application development" in lowered:
        raise ProcessingError(
            "cover letter generation returned unsafe output. Grammar quality issue detected. Fragment found: and sql and enterprise application development"
        )
    examples_include_blocks = (
        "examples include add",
        "examples include project",
        "examples include context:",
        "examples include purpose:",
        "examples include role:",
        "examples include relevant",
        "examples include todo",
    )
    for fragment in examples_include_blocks:
        if fragment in lowered:
            raise ProcessingError(
                "cover letter generation returned unsafe output. Scaffold evidence phrase detected. "
                f"Fragment found: {fragment}"
            )
    if any(fragment in lowered for fragment in ("project template", "known technical areas", "manual review notes", "candidate project leads")):
        raise ProcessingError("cover letter generation returned unsafe output. Choppy scaffold fragment detected.")
    word_count = len(re.findall(r"\b\w+\b", content))
    if word_count < 180 or word_count > 500:
        raise ProcessingError("cover letter generation returned unsafe output. Word count outside acceptable range.")


def polish_evidence_for_cover_letter(evidence_text: str) -> str:
    raw = " ".join(str(evidence_text).split()).strip()
    if not raw:
        return ""

    if is_dirty_cover_letter_text(raw):
        return ""

    lowered = raw.casefold()
    blocked = (
        "add verified academic, internship, professional, and personal projects here",
        "add verified",
        "where supported by actual projects",
        "where supported",
        "unless verified",
        "safe claims",
        "claims to avoid",
        "manual review notes",
        "project template",
        "known technical areas",
        "candidate project leads",
        "missing details",
        "technical skills to verify",
    )
    if any(marker in lowered for marker in blocked):
        return ""
    if re.search(r"\bor\b.+where supported by actual projects", lowered):
        return ""
    if re.fullmatch(r"[a-z ]{2,35}:", lowered):
        return ""
    if any(
        lowered.startswith(prefix)
        for prefix in ("context:", "purpose:", "role:", "technologies:", "what was built:", "outcome:", "safe claims:", "claims to avoid:")
    ):
        return ""

    text = raw.rstrip(". ")
    replacements = (
        (r"^Contributed\b", "contributing"),
        (r"^Implemented\b", "implementing"),
        (r"^Wrote\b", "writing"),
        (r"^Added\b", "adding"),
        (r"^Modified\b", "modifying"),
        (r"^Improved\b", "improving"),
        (r"^Tested\b", "testing"),
        (r"^Built and maintained\b", "building and maintaining"),
        (r"^Built\b", "building"),
        (r"^Worked on\b", "working on"),
        (r"^Worked in\b", "working in"),
        (r"^Applied\b", "applying"),
    )
    for pattern, replacement in replacements:
        if re.search(pattern, text):
            text = re.sub(pattern, replacement, text, count=1)
            break
    return text[:260]


def is_safe_cover_letter_evidence_fragment(text: str) -> bool:
    normalized = " ".join(str(text).split()).strip()
    if not normalized:
        return False
    if is_dirty_cover_letter_text(normalized):
        return False
    lowered = normalized.casefold()
    blocked = (
        "add verified",
        "project template",
        "where supported",
        "unless verified",
        "claims to avoid",
        "safe claims",
        "manual review notes",
        "known technical areas",
        "candidate project leads",
    )
    if any(marker in lowered for marker in blocked):
        return False
    if re.fullmatch(r"[a-z ]{2,35}:", lowered):
        return False
    return True


def is_internal_only_requirement(text: str) -> bool:
    value = text.strip().lower()
    if not value:
        return False
    patterns = (
        "minimum",
        "2-3 years",
        "years of software engineering experience",
        "required",
        "must have",
        "working knowledge",
        "bachelor",
        "degree",
        "certification",
        "microsoft 365",
        "azure",
        "power platform",
        "enterprise identity",
        "iam",
    )
    return any(pattern in value for pattern in patterns)


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
    sections: list[str] = []

    if profile_dir.is_dir():
        for filename in (
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
        ):
            content = read_optional_text_file(profile_dir / filename)
            if content:
                sections.append(f"## profile/{filename}\n{content}")

    for path_str in (
        "/app/.claude/skills/job-application-assistant/01-candidate-profile.md",
        "/app/.claude/skills/job-application-assistant/04-job-evaluation.md",
    ):
        path = Path(path_str)
        content = read_optional_text_file(path)
        if content:
            sections.append(f"## {path.as_posix().replace('/app/', '')}\n{content}")

    return "\n\n".join(sections) if sections else "Profile context unavailable."


def build_documents_context(settings: Settings) -> str:
    documents_dir = settings.app_data_dir / "documents"
    if not documents_dir.is_dir():
        return "Document context unavailable."

    sections: list[str] = []
    inventory_lines = []
    for folder_name in ("cv", "linkedin", "diplomas", "applications"):
        folder = documents_dir / folder_name
        if not folder.is_dir():
            continue
        file_names = [path.name for path in sorted(folder.iterdir()) if path.is_file() and path.name != ".gitkeep"]
        if file_names:
            inventory_lines.append(f"- {folder_name}: {', '.join(file_names)}")
    if inventory_lines:
        sections.append(
            "## Document Inventory\n"
            + "These files exist as supporting source material, but they are not authoritative unless their facts also appear in the curated profile context.\n"
            + "\n".join(inventory_lines)
        )

    return "\n\n".join(sections) if sections else "Document context unavailable."


def parse_markdown_bullets(text: str, heading: str) -> list[str]:
    pattern = re.compile(rf"^##+\s+{re.escape(heading)}\s*$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return []

    lines = text[match.end():].splitlines()
    items: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("##"):
            break
        bullet = re.match(r"^-\s+(.+)$", line)
        if bullet:
            items.append(bullet.group(1).strip())
    return items


def parse_markdown_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        heading = re.match(r"^###\s+(.+)$", line)
        if heading:
            current = heading.group(1).strip()
            sections.setdefault(current, [])
            continue
        bullet = re.match(r"^-\s+(.+)$", line)
        if bullet and current:
            sections.setdefault(current, []).append(bullet.group(1).strip())
    return sections


def first_nonempty(*values: str) -> str:
    for value in values:
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return ""


def build_fallback_cv(job: dict[str, Any], fit: dict[str, Any], targeting: str, settings: Settings) -> str:
    resume_facts = read_optional_text_file(settings.app_data_dir / "profile" / "resume_facts.md")
    skills_inventory = read_optional_text_file(settings.app_data_dir / "profile" / "skills_inventory.md")
    education_notes = read_optional_text_file(settings.app_data_dir / "profile" / "education.md")
    experience_bullets = read_optional_text_file(settings.app_data_dir / "profile" / "experience_bullets.md")

    matched_skills = [str(item).strip() for item in fit.get("matched_skills", []) if str(item).strip()]
    resume_keywords = [str(item).strip() for item in fit.get("resume_keywords_to_include", []) if str(item).strip()]
    missing_skills = [str(item).strip() for item in fit.get("missing_skills", []) if str(item).strip()]
    reasons = [str(item).strip() for item in fit.get("reasons_to_apply", []) if str(item).strip()]

    all_skills = []
    for skill in matched_skills + resume_keywords:
        if skill and skill not in all_skills:
            all_skills.append(skill)
    selected_skills = all_skills[:8]

    experience_sections = parse_markdown_sections(experience_bullets)
    uwo_bullets = experience_sections.get("UWO IT", [])[:3]
    applied_bullets = experience_sections.get("Applied Benefits", [])[:3]

    if not uwo_bullets:
        uwo_bullets = [
            "Contributed to application development work in a university IT environment.",
            "Supported debugging, technical requirements, and documentation tasks for internal users.",
        ]
    if not applied_bullets:
        applied_bullets = [
            "Supported junior software development work for business applications.",
            "Worked with C#, .NET, ASP.NET, and relational database concepts where verified by project history.",
        ]

    summary_parts = []
    role = str(job.get("title") or "the target role").strip()
    company = str(job.get("company") or "the employer").strip()
    strongest_skills = ", ".join(selected_skills[:5]) if selected_skills else "backend development, databases, and maintainable application code"
    summary_parts.append(
        f"Early-career software developer targeting {role} opportunities with relevant experience in application development, database-backed systems, and maintainable internal tools."
    )
    summary_parts.append(
        f"Relevant strengths for {company} include {strongest_skills}."
    )
    angle = first_nonempty(
        str(fit.get("suggested_resume_angle") or ""),
        first_nonempty(*reasons[:1]),
    )
    if angle:
        summary_parts.append(angle)
    summary = " ".join(summary_parts[:3])

    education_lines = []
    if "Fox Valley Technical College" in resume_facts or "Fox Valley Technical College" in education_notes:
        education_lines.append("### Fox Valley Technical College\n- Associate's degree. Exact degree title and dates to confirm before final use.")
    if "University of Wisconsin Oshkosh" in resume_facts or "University of Wisconsin Oshkosh" in education_notes:
        education_lines.append("### University of Wisconsin Oshkosh\n- Bachelor's degree. Exact degree title and dates to confirm before final use.")
    if not education_lines:
        education_lines.append("### Education\n- Verified degree information needs manual completion before final submission.")

    warnings = ""
    if missing_skills:
        warnings = "\n\n## Notes For Final Review\n- Avoid overclaiming: " + ", ".join(missing_skills[:4]) + "."

    skills_lines = "\n".join(f"- {skill}" for skill in selected_skills) if selected_skills else "- Skills should be selected manually from verified profile facts."
    uwo_lines = "\n".join(f"- {bullet}" for bullet in uwo_bullets)
    applied_lines = "\n".join(f"- {bullet}" for bullet in applied_bullets)

    return (
        "## Professional Summary\n"
        + summary
        + "\n\n## Selected Skills\n"
        + skills_lines
        + "\n\n## Experience\n"
        + "### UWO IT\n"
        + uwo_lines
        + "\n\n### Applied Benefits\n"
        + applied_lines
        + "\n\n## Education\n"
        + "\n\n".join(education_lines)
        + warnings
    )


def cv_title(job: dict[str, Any]) -> str:
    title = str(job.get("title") or "Unknown role").strip() or "Unknown role"
    company = str(job.get("company") or "Unknown company").strip() or "Unknown company"
    return f"CV Draft for {title} at {company}"


def format_cv_output(content: str, job: dict[str, Any], identity: dict[str, str]) -> str:
    title = cv_title(job)
    name = identity["name"]
    email = identity["email"]
    body = sanitize_generated_cv(content).strip()

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


def sanitize_generated_cv(content: str) -> str:
    body = content.strip()
    body = body.replace("```markdown", "")
    body = body.replace("```md", "")
    body = body.replace("```", "")
    body = body.replace("[Candidate Name]", DEFAULT_CANDIDATE_NAME)
    body = body.replace("[Email]", DEFAULT_CANDIDATE_EMAIL)
    body = body.replace("[candidate.email]", DEFAULT_CANDIDATE_EMAIL)
    body = body.replace("[candidate.phone]", "")
    body = body.replace("candidate.email", DEFAULT_CANDIDATE_EMAIL)
    body = body.replace("candidate.phone", "")

    banned_phrases = (
        "Tailored CV and Cover Letter",
        "Tailoring the Cover Letter",
        "Cover Letter for",
        "Dear Hiring Manager",
        "References available upon request",
    )
    cleaned_lines = []
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if any(phrase in line for phrase in banned_phrases):
            continue
        if re.match(r"^#+\s*(Objective|References)\b", line, re.IGNORECASE):
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def validate_generated_cv(content: str, job: dict[str, Any]) -> None:
    lowered = content.lower()
    forbidden_markers = (
        "tailored cv and cover letter",
        "tailoring the cover letter",
        "dear hiring manager",
        "cover letter for",
        "references available upon request",
        "```",
    )
    if any(marker in lowered for marker in forbidden_markers):
        raise ProcessingError(
            "CV generation returned invalid output. The model produced cover-letter or meta-analysis content instead of a CV draft."
        )

    required_sections = (
        "## professional summary",
        "## experience",
        "## education",
    )
    if not all(section in lowered for section in required_sections):
        raise ProcessingError(
            "CV generation returned incomplete output. Expected CV sections were missing."
        )

    target_company = str(job.get("company") or "").strip().lower()
    if target_company and f"worked at {target_company}" in lowered:
        raise ProcessingError(
            "CV generation returned invalid output. It appears to claim employment at the target company."
        )


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
        timeout_seconds=300,
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
        max_tokens=1200,
        response_format="text",
    )

    try:
        response = provider.complete(request)
        content = response.text.strip()
        if not content:
            raise ProcessingError("CV generation returned empty content")
        validate_generated_cv(content, job)
    except (ModelProviderError, ProcessingError):
        content = build_fallback_cv(job, fit, targeting, settings)
        validate_generated_cv(content, job)

    output_path = app_dir / "cv-draft.md"
    output_path.write_text(format_cv_output(content, job, identity), encoding="utf-8")
    return output_path


def build_cover_letter_prompt(
    letter_brief: dict[str, Any],
) -> str:
    brief_json = json.dumps(letter_brief, ensure_ascii=False, indent=2)
    return f"""Write a complete, job-specific cover letter in Markdown.

Output format:
- Start with a greeting line: "Dear Hiring Manager,"
- 3 to 4 concise paragraphs
- End with this exact sign-off block:
    Best regards,
    Vincent Morrill

Style constraints:
- Professional and direct
- No tables
- No markdown code fences
- No invented claims
- Do not include self-disqualifying language.
- Do not include exact requirement-copy language from postings.
- Do not include placeholder markers for candidate identity or template notes.
- Avoid awkward location phrasing such as "in Remote".
- Do not use "ideal candidate", "robust solutions", or "seamlessly integrate".
- Use 1 to 2 evidence cards naturally in the letter.
- Do not mention source filenames in the letter.
- Do not invent facts outside the evidence cards.
- Do not overclaim ownership, senior architecture authority, cloud ownership, certifications, or platform expertise.
- If a skill is not verified, omit it or use neutral ramping language.
- Prefer one concrete proof paragraph over broad claims.

Letter Brief JSON:
{brief_json}
"""


def build_cover_letter_brief(
    job: dict[str, Any],
    fit: dict[str, Any],
    profile_context: str,
    documents_context: str,
    identity: dict[str, str],
    letter_evidence_cards: list[dict[str, Any]] | None = None,
    max_evidence_cards: int = 10,
) -> dict[str, Any]:
    role_title = str(job.get("title") or "the target role").strip()
    company = str(job.get("company") or "the target company").strip()
    location_raw = str(job.get("location") or "").strip()
    location = ""
    if location_raw and location_raw.lower() != "remote":
        location = location_raw

    matched_skills = [
        str(item).strip()
        for item in fit.get("matched_skills", [])
        if str(item).strip()
        and not is_internal_only_requirement(str(item))
        and not is_dirty_cover_letter_text(str(item))
    ]
    safe_keywords = [
        str(item).strip()
        for item in fit.get("resume_keywords_to_include", [])
        if str(item).strip()
        and not is_internal_only_requirement(str(item))
        and not is_dirty_cover_letter_text(str(item))
    ]
    reasons_to_apply = [
        str(item).strip()
        for item in fit.get("reasons_to_apply", [])
        if str(item).strip()
        and not is_internal_only_requirement(str(item))
        and not is_dirty_cover_letter_text(str(item))
    ]
    suggested_resume_angle = str(fit.get("suggested_resume_angle") or "").strip()
    if is_internal_only_requirement(suggested_resume_angle):
        suggested_resume_angle = ""
    cover_letter_angle = str(fit.get("cover_letter_angle") or "").strip()
    if is_internal_only_requirement(cover_letter_angle):
        cover_letter_angle = ""

    enterprise_themes = [
        "enterprise application contribution",
        "internal university IT applications",
        "benefits/insurance software",
        "insurance quoting workflows",
        "unit-tested feature work",
        "C#/.NET/SQL",
    ]
    evidence_cards_raw = letter_evidence_cards if isinstance(letter_evidence_cards, list) else []
    max_cards = max(1, min(max_evidence_cards, 10))
    evidence_cards = [
        {
            "theme": str(item.get("theme") or "Evidence").strip(),
            "text": str(item.get("text") or "").strip(),
            "source_file": str(item.get("source_file") or "unknown").strip(),
            "claim_boundary": str(item.get("claim_boundary") or "Do not overclaim ownership or unverified platform expertise.").strip(),
        }
        for item in evidence_cards_raw
        if isinstance(item, dict)
        and str(item.get("text") or "").strip()
        and not is_dirty_cover_letter_text(str(item.get("text") or ""))
    ][:max_cards]

    if not evidence_cards:
        fallback_bullets = build_cover_letter_evidence(
            profile_context,
            {
                "matched_skills": matched_skills,
                "safe_resume_keywords": safe_keywords,
                "safe_enterprise_themes": enterprise_themes,
            },
        )
        evidence_cards = [
            {
                "theme": "Profile evidence",
                "text": bullet,
                "source_file": "profile_context",
                "claim_boundary": "Do not overclaim ownership or unverified platform expertise.",
            }
            for bullet in fallback_bullets[:max_cards]
            if not is_dirty_cover_letter_text(bullet)
        ]

    if not evidence_cards:
        evidence_cards = [
            {
                "theme": "Fallback-safe evidence",
                "text": "Contributed feature work, testing-focused improvements, and workflow refinements in internal and enterprise-grade application contexts.",
                "source_file": "fallback-safe",
                "claim_boundary": "Do not overclaim ownership or unverified platform expertise.",
            }
        ]

    return {
        "role_title": role_title,
        "company": company,
        "location": location,
        "candidate_name": str(identity.get("name") or DEFAULT_CANDIDATE_NAME).strip(),
        "candidate_email": str(identity.get("email") or DEFAULT_CANDIDATE_EMAIL).strip(),
        "matched_skills": matched_skills[:8],
        "safe_resume_keywords": safe_keywords[:10],
        "reasons_to_apply": reasons_to_apply[:6],
        "suggested_resume_angle": suggested_resume_angle,
        "cover_letter_angle": cover_letter_angle,
        "safe_enterprise_themes": enterprise_themes,
        "evidence_cards": evidence_cards,
        "document_inventory_context": documents_context,
    }


def validate_cover_letter_brief(letter_brief: dict[str, Any], allow_generic_evidence: bool = False) -> None:
    role = str(letter_brief.get("role_title") or "").strip()
    company = str(letter_brief.get("company") or "").strip()
    candidate_name = str(letter_brief.get("candidate_name") or "").strip()
    if not role:
        raise ProcessingError("cover letter brief invalid: role_title is required")
    if not company:
        raise ProcessingError("cover letter brief invalid: company is required")
    if candidate_name != DEFAULT_CANDIDATE_NAME:
        raise ProcessingError("cover letter brief invalid: candidate_name mismatch")

    serialized = json.dumps(letter_brief, ensure_ascii=False).casefold()
    if "minimum 2-3 years" in serialized or "todo" in serialized:
        raise ProcessingError("cover letter brief invalid: contains internal requirement or TODO text")

    for item in letter_brief.get("matched_skills", []):
        value = str(item).strip()
        if is_internal_only_requirement(value) or is_dirty_cover_letter_text(value):
            raise ProcessingError("cover letter brief invalid: contains unsafe matched skill")
    for item in letter_brief.get("safe_resume_keywords", []):
        value = str(item).strip()
        if is_internal_only_requirement(value) or is_dirty_cover_letter_text(value):
            raise ProcessingError("cover letter brief invalid: contains unsafe keyword")

    cards = [card for card in letter_brief.get("evidence_cards", []) if isinstance(card, dict)]
    clean_cards = [card for card in cards if not is_dirty_cover_letter_text(str(card.get("text") or ""))]
    if not clean_cards and not allow_generic_evidence:
        raise ProcessingError("cover letter brief invalid: no clean evidence cards")


def build_cover_letter_evidence(profile_context: str, letter_brief: dict[str, Any]) -> list[str]:
    text = profile_context.casefold()
    evidence: list[str] = []

    def add_once(item: str) -> None:
        if item not in evidence:
            evidence.append(item)

    if "bizlink" in text:
        add_once(
            "Contributed feature work and unit-tested business-rule changes in BizLink, an enterprise-grade insurance quoting workflow."
        )
    if any(token in text for token in ("uwo", "portal", "rostar", "university it")):
        add_once(
            "Contributed to internal university IT applications with backend and application development, debugging, and documentation work."
        )
    if any(token in text for token in ("applied benefits", "applied systems", "benefits", "insurance software")):
        add_once(
            "Worked on benefits and insurance software involving business workflows, automation, testing, and pull-request-based development."
        )
    if any(token in text for token in ("c#", ".net", "sql", "angular", "typescript")):
        add_once(
            "Built and maintained application features using C#, .NET, and SQL with practical collaboration in code review and testing workflows."
        )
    if any(token in text for token in ("unit test", "unit-tested", "testing")):
        add_once(
            "Implemented and refined unit-tested feature work to improve behavior reliability and maintainability in production-facing workflows."
        )

    if not evidence:
        skills = [str(item).strip() for item in letter_brief.get("matched_skills", []) if str(item).strip()]
        top_skills = skills[:3]
        if top_skills:
            skill_line = ", ".join(top_skills)
            add_once(f"Contributed feature work and testing-focused improvements in application development contexts using {skill_line}.")
        else:
            add_once("Contributed feature work, debugging support, and testing-focused improvements in internal application workflows.")

    return evidence[:6]


def build_cover_letter_critique_prompt(letter_brief: dict[str, Any], draft: str) -> str:
    brief_json = json.dumps(letter_brief, ensure_ascii=False, indent=2)
    return f"""Evaluate this draft against the rubric and provide concise revision bullets.

Letter Brief JSON:
{brief_json}

Draft:
{draft}

Rubric:
- no unsupported claims
- no self-disqualifying gap language
- no exact "Minimum 2-3 years" requirement language
- no bracket placeholders
- signs as Vincent Morrill
- 3 to 4 concise paragraphs
- company and role are named naturally
- includes at least one grounded experience theme:
  - enterprise application contribution
  - internal university IT applications
  - benefits/insurance software
  - insurance quoting workflows
  - unit-tested feature work
  - C#/.NET/SQL
- avoids awkward location phrasing like "in Remote"
- avoids generic filler such as "robust solutions that seamlessly integrate"
- avoids saying "ideal candidate"
- sounds confident but not inflated
- uses 1 to 2 concrete evidence cards naturally
"""


def build_cover_letter_final_prompt(
    letter_brief: dict[str, Any],
    draft: str,
    critique: str,
) -> str:
    brief_json = json.dumps(letter_brief, ensure_ascii=False, indent=2)
    return f"""Rewrite the cover letter as a polished final version.

Requirements:
- Start with: Dear Hiring Manager,
- End exactly with:
  Best regards,

  Vincent Morrill
- 3 to 4 concise paragraphs before the sign-off
- no headings, no bullets, no code fences
- use 1 to 2 concrete evidence cards from evidence_cards
- avoid generic filler and avoid broad claims without concrete evidence

Letter Brief JSON:
{brief_json}

Draft:
{draft}

Critique:
{critique}
"""


def build_cover_letter_repair_prompt(
        letter_brief: dict[str, Any],
        unsafe_output: str,
        validation_error: str,
) -> str:
        brief_json = json.dumps(letter_brief, ensure_ascii=False, indent=2)
        return f"""Rewrite this cover letter so it is safe and submission-ready.

Rules:
- Return only the corrected cover letter.
- Start with: Dear Hiring Manager,
- End with:
    Best regards,

    Vincent Morrill
- Remove all scaffold, TODO, template, and instruction text.
- Use only factual content from the Letter Brief evidence_cards.
- Keep the letter factual, concise, and sendable.

Letter Brief JSON:
{brief_json}

Unsafe output:
{unsafe_output}

Validation error:
{validation_error}
"""


def build_fallback_cover_letter(letter_brief: dict[str, Any]) -> str:
    role_title = str(letter_brief.get("role_title") or "Application Services Software Engineer").strip()
    company = str(letter_brief.get("company") or "the company").strip()
    stack = "C#, .NET Core, SQL, and enterprise application development"
    evidence_cards = [item for item in letter_brief.get("evidence_cards", []) if isinstance(item, dict)]
    evidence_cards.sort(
        key=lambda item: 0 if str(item.get("source_file") or "") == "cover_letter_evidence.md" else 1
    )
    evidence_texts = [
        str(item.get("text") or "").strip()
        for item in evidence_cards
        if str(item.get("text") or "").strip() and not is_dirty_cover_letter_text(str(item.get("text") or ""))
    ]
    polished_evidence = [polish_evidence_for_cover_letter(item) for item in evidence_texts]
    polished_evidence = [item for item in polished_evidence if is_safe_cover_letter_evidence_fragment(item)]

    if polished_evidence:
        evidence_clause = f"Examples include {polished_evidence[0]}, giving me a practical foundation for supporting software used in real operational workflows."
    else:
        evidence_clause = "This hands-on work has given me a practical foundation for supporting software used in real operational workflows."

    letter = (
        "Dear Hiring Manager,\n\n"
        f"I am applying for the {role_title} position at {company}. My background in {stack} aligns with the role's focus on maintaining and improving business-critical application services.\n\n"
        "In recent development work, I have contributed to internal and enterprise-grade systems by implementing features, writing unit tests, improving workflow behavior, and working through pull-request-based development. "
        f"{evidence_clause} This work required translating business rules into reliable behavior, validating changes with tests, and delivering improvements that can be maintained by teams over time.\n\n"
        "What interests me about this role is the mix of software development, stakeholder support, and production-minded problem solving. I would bring a grounded engineering approach, careful attention to maintainability, and a willingness to ramp into the team's platform environment where needed. I focus on practical delivery, clear implementation details, and stable software behavior in day-to-day operations.\n\n"
        "Thank you for your time and consideration. I would welcome the opportunity to discuss how my application development experience can support your team. I am available to share concrete examples of feature delivery, testing-focused improvements, and workflow refinements that align with this role.\n\n"
        "Best regards,\n\n"
        "Vincent Morrill"
    )
    return letter


def generate_cover_letter_with_review(
    job: dict[str, Any],
    fit: dict[str, Any],
    profile_context: str,
    documents_context: str,
    identity: dict[str, str],
    settings: Settings | None,
    provider: ModelProvider,
    review_passes: bool = True,
    query_callback: Any | None = None,
) -> CoverLetterGenerationResult:
    actual_queries = 0
    evidence_query_count_actual = 0
    dirty_evidence_rejected: list[str] = []
    repair_attempted = False
    repair_successful = False

    max_evidence_queries = settings.cover_letter_max_evidence_queries if settings is not None else 10
    max_evidence_cards = settings.cover_letter_max_evidence_cards if settings is not None else 10
    max_model_calls = settings.cover_letter_max_model_calls if settings is not None else 8
    repair_passes = settings.cover_letter_repair_passes if settings is not None else 1

    def record_query(stage: str) -> None:
        nonlocal actual_queries
        actual_queries += 1
        if callable(query_callback):
            query_callback(stage)

    def can_call_model() -> bool:
        return actual_queries < max_model_calls

    def maybe_record_dirty(text: str) -> None:
        normalized = " ".join(text.split()).strip()
        if not normalized:
            return
        if normalized not in dirty_evidence_rejected:
            dirty_evidence_rejected.append(normalized[:220])

    evidence_cards: list[dict[str, Any]] = []
    sources = list(ALLOWED_PROFILE_EVIDENCE_FILES)
    if settings is not None:
        try:
            source_inventory = list_profile_evidence_sources(settings)
            available_sources = [
                str(item.get("source_file") or "")
                for item in source_inventory
                if isinstance(item, dict) and item.get("exists")
            ]
            sources = [src for src in sources if src in set(available_sources)] or list(ALLOWED_PROFILE_EVIDENCE_FILES)
        except Exception:
            sources = list(ALLOWED_PROFILE_EVIDENCE_FILES)

        deterministic_queries = build_evidence_queries(job, fit)[:max_evidence_queries]
        planned_queries = deterministic_queries
        if can_call_model():
            record_query("evidence-planning-pass")
            planned_queries = plan_cover_letter_evidence_queries(job, fit, provider, max_queries=max_evidence_queries)
        merged_queries: list[str] = []
        for query in planned_queries + deterministic_queries:
            normalized = str(query).strip()
            if normalized and normalized not in merged_queries:
                merged_queries.append(normalized)
        merged_queries = merged_queries[:max_evidence_queries]

        themes = [
            "C#/.NET/SQL",
            "enterprise application development",
            "internal applications",
            "insurance and benefits workflows",
            "unit-tested feature work",
            "pull-request-based development",
        ]
        for query in merged_queries:
            evidence_query_count_actual += 1
            try:
                cards = search_profile_evidence(settings, query, themes, max_results=max_evidence_cards)
            except ValueError:
                continue
            for card in cards:
                text = str(card.get("text") or "")
                if is_dirty_cover_letter_text(text):
                    maybe_record_dirty(text)
                    continue
                if card not in evidence_cards:
                    evidence_cards.append(card)
            if len(evidence_cards) >= max_evidence_cards:
                break

    letter_brief = build_cover_letter_brief(
        job,
        fit,
        profile_context,
        documents_context,
        identity,
        letter_evidence_cards=evidence_cards,
        max_evidence_cards=max_evidence_cards,
    )

    try:
        validate_cover_letter_brief(letter_brief)
    except ProcessingError:
        repaired_cards = []
        for card in letter_brief.get("evidence_cards", []):
            if not isinstance(card, dict):
                continue
            text = str(card.get("text") or "").strip()
            if not text or is_dirty_cover_letter_text(text):
                maybe_record_dirty(text)
                continue
            repaired_cards.append(card)
        if not repaired_cards:
            repaired_cards = [
                {
                    "theme": "Fallback-safe evidence",
                    "text": "Contributed feature work, testing-focused improvements, and workflow refinements in internal and enterprise-grade application contexts.",
                    "source_file": "fallback-safe",
                    "claim_boundary": "Do not overclaim ownership or unverified platform expertise.",
                }
            ]
        letter_brief["evidence_cards"] = repaired_cards[:max_evidence_cards]
        validate_cover_letter_brief(letter_brief, allow_generic_evidence=True)

    fallback_content = sanitize_generated_cover_letter(build_fallback_cover_letter(letter_brief))
    fallback_note = None
    if "Examples include" not in fallback_content:
        fallback_note = "No clean evidence cards were available."
    expected_queries = max_model_calls
    tool_access = {
        "enabled": settings is not None,
        "allowed_sources": list(ALLOWED_PROFILE_EVIDENCE_FILES),
    }

    def cards_used_from_content(content: str, cards: list[dict[str, Any]], limit: int = 2) -> list[dict[str, str]]:
        lowered = content.casefold()
        ranked: list[tuple[int, dict[str, Any]]] = []
        for card in cards:
            text = str(card.get("text") or "").strip()
            if not text:
                continue
            tokens = [token for token in re.findall(r"[a-zA-Z0-9#.+-]+", text.casefold()) if len(token) > 3]
            overlap = sum(1 for token in set(tokens[:12]) if token in lowered)
            if overlap > 0:
                ranked.append((overlap, card))
        ranked.sort(key=lambda item: item[0], reverse=True)
        picked = [item[1] for item in ranked[:limit]]
        if not picked:
            picked = cards[:limit]
        return [
            {
                "theme": str(item.get("theme") or "Evidence").strip(),
                "source_file": str(item.get("source_file") or "unknown").strip(),
            }
            for item in picked
        ]

    def fallback_result(reason: str, validation_error: str | None = None) -> CoverLetterGenerationResult:
        sanitized_reason = sanitize_cover_letter_reason(reason)
        if fallback_note:
            sanitized_reason = f"{sanitized_reason} {fallback_note}".strip()
        validate_generated_cover_letter(fallback_content)
        return CoverLetterGenerationResult(
            content=fallback_content,
            source="deterministic-fallback",
            review_passes_enabled=review_passes,
            model_query_count_expected=expected_queries,
            model_query_count_actual=actual_queries,
            evidence_query_count_actual=evidence_query_count_actual,
            dirty_evidence_rejected_count=len(dirty_evidence_rejected),
            evidence_cards_used=cards_used_from_content(fallback_content, letter_brief.get("evidence_cards", []), limit=2),
            evidence_cards_rejected_sample=[sanitize_cover_letter_reason(item) for item in dirty_evidence_rejected[:3]],
            tool_access=tool_access,
            repair_attempted=repair_attempted,
            repair_successful=repair_successful,
            fallback_reason=sanitized_reason,
            validation_error=validation_error,
        )

    try:
        if review_passes:
            if not can_call_model():
                return fallback_result("model call budget exhausted before draft pass")
            record_query("draft-pass")
            draft_response = provider.complete(
                ModelRequest(
                    system_prompt=COVER_LETTER_DRAFT_SYSTEM_PROMPT,
                    user_prompt=build_cover_letter_prompt(letter_brief),
                    temperature=0.3,
                    max_tokens=900,
                    response_format="text",
                )
            )
            draft = sanitize_generated_cover_letter(draft_response.text)
            if not draft:
                return fallback_result("final model output failed validation: cover letter generation returned empty draft")

            if not can_call_model():
                return fallback_result("model call budget exhausted before critique pass")
            record_query("critique-pass")
            critique_response = provider.complete(
                ModelRequest(
                    system_prompt=COVER_LETTER_CRITIQUE_SYSTEM_PROMPT,
                    user_prompt=build_cover_letter_critique_prompt(letter_brief, draft),
                    temperature=0,
                    max_tokens=500,
                    response_format="text",
                )
            )
            critique = critique_response.text.strip()

            if not can_call_model():
                return fallback_result("model call budget exhausted before final rewrite pass")
            record_query("final-rewrite-pass")
            final_response = provider.complete(
                ModelRequest(
                    system_prompt=COVER_LETTER_FINAL_SYSTEM_PROMPT,
                    user_prompt=build_cover_letter_final_prompt(letter_brief, draft, critique),
                    temperature=0.2,
                    max_tokens=900,
                    response_format="text",
                )
            )
            content = sanitize_generated_cover_letter(final_response.text)
            source = "model-final"
        else:
            if not can_call_model():
                return fallback_result("model call budget exhausted before single pass")
            record_query("single-pass")
            response = provider.complete(
                ModelRequest(
                    system_prompt=COVER_LETTER_SINGLE_PASS_SYSTEM_PROMPT,
                    user_prompt=build_cover_letter_prompt(letter_brief),
                    temperature=0.3,
                    max_tokens=900,
                    response_format="text",
                )
            )
            content = sanitize_generated_cover_letter(response.text)
            source = "model-single-pass"

        if not content:
            return fallback_result("final model output failed validation: cover letter generation returned empty content")
        try:
            validate_generated_cover_letter(content)
        except ProcessingError as exc:
            validation_error_clean = sanitize_cover_letter_reason(str(exc))
            if repair_passes > 0 and can_call_model():
                repair_attempted = True
                record_query("repair-pass")
                repair_response = provider.complete(
                    ModelRequest(
                        system_prompt=COVER_LETTER_FINAL_SYSTEM_PROMPT,
                        user_prompt=build_cover_letter_repair_prompt(letter_brief, content, validation_error_clean),
                        temperature=0,
                        max_tokens=900,
                        response_format="text",
                    )
                )
                repaired = sanitize_generated_cover_letter(repair_response.text)
                if repaired:
                    try:
                        validate_generated_cover_letter(repaired)
                        repair_successful = True
                        return CoverLetterGenerationResult(
                            content=repaired,
                            source="model-repaired",
                            review_passes_enabled=review_passes,
                            model_query_count_expected=expected_queries,
                            model_query_count_actual=actual_queries,
                            evidence_query_count_actual=evidence_query_count_actual,
                            dirty_evidence_rejected_count=len(dirty_evidence_rejected),
                            evidence_cards_used=cards_used_from_content(repaired, letter_brief.get("evidence_cards", []), limit=2),
                            evidence_cards_rejected_sample=[sanitize_cover_letter_reason(item) for item in dirty_evidence_rejected[:3]],
                            tool_access=tool_access,
                            repair_attempted=repair_attempted,
                            repair_successful=repair_successful,
                            fallback_reason=None,
                            validation_error=None,
                        )
                    except ProcessingError:
                        pass
            reason = f"final model output failed validation: {exc}"
            return fallback_result(reason, validation_error=validation_error_clean)

        return CoverLetterGenerationResult(
            content=content,
            source=source,
            review_passes_enabled=review_passes,
            model_query_count_expected=expected_queries,
            model_query_count_actual=actual_queries,
            evidence_query_count_actual=evidence_query_count_actual,
            dirty_evidence_rejected_count=len(dirty_evidence_rejected),
            evidence_cards_used=cards_used_from_content(content, letter_brief.get("evidence_cards", []), limit=2),
            evidence_cards_rejected_sample=[sanitize_cover_letter_reason(item) for item in dirty_evidence_rejected[:3]],
            tool_access=tool_access,
            repair_attempted=repair_attempted,
            repair_successful=repair_successful,
            fallback_reason=None,
            validation_error=None,
        )
    except ModelProviderError as exc:
        reason = f"model generation failed: {exc}"
        return fallback_result(reason)


def sanitize_cover_letter_reason(reason: str) -> str:
    cleaned = reason.strip()
    cleaned = re.sub(r"(?i)(x-api-key\s*[:=]\s*)([^\s,;]+)", r"\1[redacted]", cleaned)
    cleaned = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,;]+)", r"\1[redacted]", cleaned)
    cleaned = re.sub(r"(?i)(bearer\s+)([a-z0-9._\-]+)", r"\1[redacted]", cleaned)
    return cleaned[:500]


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
- Never use referee names, reference-letter employers, or document filenames as resume content.
- Never claim the candidate worked at the target company unless that appears in verified candidate context.
- Never introduce employers such as TechCorp, Innovate Solutions, Applied Systems, or Sprecher Brewing unless they appear in the verified candidate context.

Required shape:
- Write concise Markdown only.
- Do not use code fences.
- Do not include any explanatory text before or after the CV.
- Use organization-based experience headings when exact titles are unknown.
- If dates are unknown, omit the date line entirely.
- Keep the summary to 2 or 3 sentences.
- Keep the skills section selective and job-relevant.
- For education, use only verified institutions from the candidate context.

Preferred experience pattern:
## Experience
### UWO IT
- concise relevant bullet
- concise relevant bullet

### Applied Benefits
- concise relevant bullet
- concise relevant bullet

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
