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
from app.services.evidence_tools import APPLICANT_FACING_EVIDENCE_FILES
from app.services.evidence_tools import ALLOWED_PROFILE_EVIDENCE_FILES
from app.services.evidence_tools import CoverLetterQualityIssue
from app.services.evidence_tools import REFERENCE_CONTEXT_FILES
from app.services.evidence_tools import build_evidence_queries
from app.services.evidence_tools import is_dirty_cover_letter_text
from app.services.evidence_tools import is_subjectless_action_fragment
from app.services.evidence_tools import list_existing_profile_evidence_sources
from app.services.evidence_tools import list_profile_evidence_sources
from app.services.evidence_tools import lint_cover_letter_text
from app.services.evidence_tools import plan_cover_letter_evidence_queries
from app.services.evidence_tools import search_applicant_facing_evidence
from app.services.evidence_tools import split_cover_letter_sentences
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
    evidence_mode: str
    applicant_facing_evidence_sources: list[str]
    reference_context_sources: list[str]
    tool_access: dict[str, Any]
    repair_attempted: bool = False
    repair_successful: bool = False
    fallback_reason: str | None = None
    validation_error: str | None = None


DEFAULT_CANDIDATE_NAME = "Vincent Morrill"
DEFAULT_CANDIDATE_EMAIL = "vince.codefactory@outlook.com"
GENERIC_SAFE_EVIDENCE_SENTENCE = (
    "My recent development work has included feature contributions, testing-focused improvements, and workflow refinements in internal and enterprise-grade application contexts."
)
STYLE_LIMITED_PHRASES = (
    "practical",
    "reliable",
    "operational",
    "maintainability",
    "stable",
    "business-critical",
)
BLOCKED_CLOSING_PHRASES = (
    "i am available to share concrete examples",
    "i am motivated to contribute in a role where",
)
CONCRETE_EVIDENCE_ANCHORS = (
    "bizlink",
    "insurance quoting",
    "insurance workflow",
    "benefits",
    "applied systems",
    "applied benefits",
    "uwo",
    "university",
    "portal",
    "rostar",
    "pull request",
    "unit test",
    "unit-tested",
    "workflow behavior",
    "business-rule",
    "c#",
    ".net",
    "sql",
    "angular",
    "typescript",
)
POLISHED_GENERIC_EVIDENCE_MARKERS = (
    "feature contributions",
    "testing-focused improvements",
    "workflow refinements",
    "internal and enterprise-grade application contexts",
)
OVERLY_GENERIC_SENTENCE_MARKERS = (
    "practical foundation",
    "supporting software used in real operational workflows",
    "software used in real operational workflows",
    "stable software behavior",
    "careful attention to maintainability",
    "grounded engineering approach",
    "production-minded problem solving",
    "willingness to ramp",
    "clear implementation details",
    "hands-on development work",
    "development work gives me",
    "this work required",
)
REPEATED_NOUN_STACK_PHRASES = (
    "hands-on development work",
    "development work",
    "this work",
)
PARAGRAPH_ONE_WORK_STYLE_TERMS = (
    "handoff",
    "testing",
    "follow-through",
    "day-to-day operations",
    "people using the software",
    "validation",
    "maintainability",
)
PARAGRAPH_TWO_EVIDENCE_TERMS = (
    "implemented",
    "unit tests",
    "workflow",
    "business rules",
    "pull-request",
    "university it",
    "insurance",
    "bizlink",
    "benefits",
)
PARAGRAPH_OVERLAP_TERMS = (
    "testing",
    "handoff",
    "workflow",
    "maintainable",
    "reliable",
    "operational",
    "day-to-day",
    "validation",
    "software quality",
)
ROLE_SENTENCE_STOPWORDS = {
    "about",
    "across",
    "after",
    "aligns",
    "application",
    "because",
    "bring",
    "changes",
    "company",
    "could",
    "experience",
    "focus",
    "having",
    "internal",
    "position",
    "recent",
    "role",
    "software",
    "systems",
    "their",
    "these",
    "through",
    "using",
    "where",
    "which",
    "would",
}
ACTION_FRAGMENT_EXAMPLES_INCLUDE_BLOCKS = (
    "examples include contributed",
    "examples include implemented",
    "examples include worked",
    "examples include added",
    "examples include modified",
    "examples include improved",
)

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
    "only use verified facts",
    "treat this file",
    "safe source",
    "safe themes",
    "verify",
    "expand",
    "where verified",
    "claim boundary",
    "manual review",
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
            app_dir=app_dir,
        )
    except ModelProviderError as exc:
        raise ProcessingError(f"cover letter generation failed: {exc}") from exc

    output_path = app_dir / "cover-letter.md"
    output_path.write_text(result.content + "\n", encoding="utf-8")
    meta = {
        "source": result.source,
        "evidence_mode": result.evidence_mode,
        "applicant_facing_evidence_sources": result.applicant_facing_evidence_sources,
        "reference_context_sources": result.reference_context_sources,
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

    def matched_fragment(fragment: str) -> str:
        match = re.search(re.escape(fragment), content, re.IGNORECASE)
        return match.group(0) if match else fragment

    issues = lint_cover_letter_text(content)
    if issues:
        issue = issues[0]
        sentence = " ".join(issue.sentence.split())[:220]
        raise ProcessingError(
            "cover letter generation returned unsafe output. "
            f"Sentence rule {issue.rule} failed: \"{sentence}\""
        )
    if is_dirty_cover_letter_text(content):
        raise ProcessingError("cover letter generation returned unsafe output. Dirty scaffold/template text detected.")
    for phrase in COVER_LETTER_BLOCKED_PHRASES:
        if phrase in lowered:
            raise ProcessingError(
                "cover letter generation returned unsafe output. "
                f"Blocked phrase found: {matched_fragment(phrase)}"
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
        "examples include contributed",
        "examples include implemented",
        "examples include worked",
        "examples include added",
        "examples include modified",
        "examples include improved",
        "examples include project",
        "examples include context:",
        "examples include purpose:",
        "examples include role:",
        "examples include tags:",
        "examples include tag:",
        "examples include keywords:",
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
    root_level_scaffold_markers = (
        "only use verified facts",
        "treat this file",
        "safe source",
        "safe themes",
        "verify",
        "expand",
        "todo",
        "placeholder",
        "where supported",
        "where verified",
        "claim boundary",
        "claims to avoid",
        "do not claim",
        "manual review",
        "project template",
        "relevant coursework",
    )
    for marker in root_level_scaffold_markers:
        if marker in lowered:
            raise ProcessingError(
                "cover letter generation returned unsafe output. "
                f"Instructional/scaffold source text detected: {matched_fragment(marker)}"
            )

    body_paragraphs = _cover_letter_body_paragraphs(content)
    if len(body_paragraphs) > 4:
        raise ProcessingError("cover letter generation returned unsafe output. More than 4 body paragraphs detected.")

    for phrase in BLOCKED_CLOSING_PHRASES:
        if phrase in lowered:
            raise ProcessingError(
                "cover letter generation returned unsafe output. "
                f"Closing filler detected: {matched_fragment(phrase)}"
            )

    if "the team's platform environment" in lowered:
        raise ProcessingError(
            "cover letter generation returned unsafe output. Company-specific phrasing required instead of 'the team's platform environment'."
        )

    for phrase in STYLE_LIMITED_PHRASES:
        count = lowered.count(phrase)
        if count > 1:
            raise ProcessingError(
                "cover letter generation returned unsafe output. "
                f"Style warning: '{matched_fragment(phrase)}' appears too often ({count} uses)."
            )

    for index, paragraph in enumerate(body_paragraphs):
        _validate_cover_letter_paragraph(paragraph, index)

    paragraph_role_issues = lint_cover_letter_paragraph_roles(content)
    if paragraph_role_issues:
        issue = paragraph_role_issues[0]
        paragraph_text = " ".join(issue.sentence.split())[:220]
        raise ProcessingError(
            "cover letter generation returned unsafe output. "
            f"Paragraph role rule {issue.rule} failed: \"{paragraph_text}\""
        )

    word_count = len(re.findall(r"\b\w+\b", content))
    if word_count < 180 or word_count > 500:
        raise ProcessingError("cover letter generation returned unsafe output. Word count outside acceptable range.")


def _cover_letter_body_paragraphs(content: str) -> list[str]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", content.strip()) if block.strip()]
    if not blocks:
        return []

    signoff_index = next((index for index, block in enumerate(blocks) if block.startswith("Best regards,")), len(blocks))
    return [block for block in blocks[:signoff_index] if not block.startswith("Dear Hiring Manager,")]


def _sentence_has_concrete_evidence_anchor(sentence: str) -> bool:
    lowered = sentence.casefold()
    return any(marker in lowered for marker in CONCRETE_EVIDENCE_ANCHORS)


def _is_polished_generic_evidence_sentence(sentence: str) -> bool:
    lowered = sentence.casefold()
    return sum(1 for marker in POLISHED_GENERIC_EVIDENCE_MARKERS if marker in lowered) >= 2


def _is_overly_generic_sentence(sentence: str) -> bool:
    lowered = sentence.casefold()
    if _sentence_has_concrete_evidence_anchor(sentence):
        return False
    return any(marker in lowered for marker in OVERLY_GENERIC_SENTENCE_MARKERS)


def _paragraph_term_hits(paragraph: str, terms: tuple[str, ...]) -> set[str]:
    lowered = paragraph.casefold()
    return {term for term in terms if term in lowered}


def _significant_sentence_tokens(sentence: str) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9#.+-]+", sentence.casefold())
        if len(token) >= 5 and token not in ROLE_SENTENCE_STOPWORDS
    }
    return tokens


def _sentences_repeat_evidence(left: str, right: str) -> bool:
    left_tokens = _significant_sentence_tokens(left)
    right_tokens = _significant_sentence_tokens(right)
    overlap = left_tokens & right_tokens
    evidence_overlap = overlap & {
        "bizlink",
        "workflow",
        "insurance",
        "benefits",
        "business",
        "rules",
        "university",
        "applications",
        "feature",
        "changes",
    }
    return len(overlap) >= 5 and bool(evidence_overlap)


def lint_cover_letter_paragraph_roles(text: str) -> list[CoverLetterQualityIssue]:
    body_paragraphs = _cover_letter_body_paragraphs(text)
    issues: list[CoverLetterQualityIssue] = []

    def add(rule: str, message: str, paragraph: str) -> None:
        issues.append(CoverLetterQualityIssue(rule=rule, message=message, sentence=paragraph))

    if len(body_paragraphs) >= 1:
        paragraph_one_hits = _paragraph_term_hits(body_paragraphs[0], PARAGRAPH_ONE_WORK_STYLE_TERMS)
        if len(paragraph_one_hits) > 1:
            add(
                "paragraph_role_p1_too_much_work_style",
                "Paragraph 1 should stay focused on role fit instead of detailed work-style claims",
                body_paragraphs[0],
            )

    if len(body_paragraphs) >= 2:
        paragraph_two_hits = _paragraph_term_hits(body_paragraphs[1], PARAGRAPH_TWO_EVIDENCE_TERMS)
        if not paragraph_two_hits:
            add(
                "paragraph_role_p2_missing_evidence",
                "Paragraph 2 should contain one concrete evidence anchor",
                body_paragraphs[1],
            )

    if len(body_paragraphs) >= 3:
        second_sentences = split_cover_letter_sentences(body_paragraphs[1])
        third_sentences = split_cover_letter_sentences(body_paragraphs[2])
        for second_sentence in second_sentences:
            for third_sentence in third_sentences:
                if _sentences_repeat_evidence(second_sentence, third_sentence):
                    add(
                        "paragraph_role_p3_repeats_p2_evidence",
                        "Paragraph 3 should not restate the evidence sentence from paragraph 2",
                        body_paragraphs[2],
                    )
                    break
            if issues and issues[-1].rule == "paragraph_role_p3_repeats_p2_evidence":
                break

    for index in range(len(body_paragraphs) - 1):
        overlap = _paragraph_term_hits(body_paragraphs[index], PARAGRAPH_OVERLAP_TERMS) & _paragraph_term_hits(
            body_paragraphs[index + 1], PARAGRAPH_OVERLAP_TERMS
        )
        if len(overlap) >= 3:
            add(
                "paragraph_role_adjacent_overlap",
                "Adjacent paragraphs repeat too many of the same generic concepts",
                body_paragraphs[index + 1],
            )

    return issues


def _validate_cover_letter_paragraph(paragraph: str, paragraph_index: int) -> None:
    lowered = paragraph.casefold()
    sentences = split_cover_letter_sentences(paragraph)
    if not sentences:
        return

    if is_subjectless_action_fragment(sentences[0]):
        raise ProcessingError(
            "cover letter generation returned unsafe output. Paragraph begins with a subjectless action-verb fragment."
        )

    for sentence in sentences:
        if is_subjectless_action_fragment(sentence):
            raise ProcessingError(
                "cover letter generation returned unsafe output. Sentence begins with a subjectless action-verb fragment."
            )

    for fragment in ACTION_FRAGMENT_EXAMPLES_INCLUDE_BLOCKS:
        if fragment in lowered:
            raise ProcessingError(
                "cover letter generation returned unsafe output. Examples-include phrase embeds a bullet fragment instead of prose."
            )

    this_starts = [index for index, sentence in enumerate(sentences) if re.match(r"(?i)^this\b", sentence)]
    for left, right in zip(this_starts, this_starts[1:]):
        if right - left <= 2:
            raise ProcessingError(
                "cover letter generation returned unsafe output. Nearby sentences in one paragraph begin with 'This'."
            )

    noun_stack_hits = sum(lowered.count(phrase) for phrase in REPEATED_NOUN_STACK_PHRASES)
    if noun_stack_hits >= 2:
        raise ProcessingError(
            "cover letter generation returned unsafe output. Repeated noun stacking detected in one paragraph."
        )

    for left, right in zip(sentences, sentences[1:]):
        left_lower = left.casefold()
        right_lower = right.casefold()
        if "supporting software used in real operational workflows" in left_lower and any(
            marker in right_lower for marker in ("operational workflows", "workflow", "supporting software", "software used")
        ):
            raise ProcessingError(
                "cover letter generation returned unsafe output. Back-to-back sentences repeat the same workflow-support concept."
            )

    has_concrete_anchor = any(_sentence_has_concrete_evidence_anchor(sentence) for sentence in sentences)
    overly_generic_count = sum(1 for sentence in sentences if _is_overly_generic_sentence(sentence))
    if not has_concrete_anchor and overly_generic_count > 1:
        raise ProcessingError(
            "cover letter generation returned unsafe output. Paragraph uses multiple generic sentences without a concrete evidence anchor."
        )

    if paragraph_index == 1:
        has_generic_evidence = any(_is_polished_generic_evidence_sentence(sentence) for sentence in sentences)
        if not has_concrete_anchor and not has_generic_evidence:
            raise ProcessingError(
                "cover letter generation returned unsafe output. Second paragraph needs one concrete evidence anchor or one polished generic evidence sentence."
            )
        if has_generic_evidence:
            generic_follow_up_count = sum(
                1
                for sentence in sentences
                if not _is_polished_generic_evidence_sentence(sentence) and _is_overly_generic_sentence(sentence)
            )
            if generic_follow_up_count > 0:
                raise ProcessingError(
                    "cover letter generation returned unsafe output. Second paragraph stacks a generic evidence sentence with another generic follow-up sentence."
                )


def _clip_cover_letter_sentence(text: str, limit: int = 220) -> str:
    normalized = " ".join(str(text).split()).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def format_cover_letter_quality_issue(
    prefix: str,
    issue: CoverLetterQualityIssue,
    field_path: str | None = None,
) -> str:
    sentence = _clip_cover_letter_sentence(issue.sentence, limit=220)
    location = f"{field_path} " if field_path else ""
    return f"{prefix}: {location}failed sentence rule {issue.rule}: \"{sentence}\""


def _write_cover_letter_brief_error(
    app_dir: Path | None,
    *,
    error_message: str,
    field_path: str | None = None,
    rule: str | None = None,
    offending_sentence: str | None = None,
    source_file: str | None = None,
    evidence_card_index: int | None = None,
) -> None:
    if app_dir is None or not app_dir.is_dir():
        return
    payload = {
        "error_message": error_message,
        "field_path": field_path,
        "rule": rule,
        "offending_sentence": _clip_cover_letter_sentence(offending_sentence or "", limit=220) if offending_sentence else None,
        "source_file": source_file,
        "evidence_card_index": evidence_card_index,
    }
    try:
        (app_dir / "cover-letter.brief-error.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        logger.debug("Unable to write cover-letter.brief-error.json", exc_info=True)


def evidence_fragment_to_clause(text: str) -> str:
    normalized = " ".join(str(text).split()).strip().rstrip(". ")
    if not normalized:
        return ""
    if normalized[:1].isupper():
        normalized = normalized[:1].lower() + normalized[1:]
    return normalized


def evidence_fragment_to_sentence(text: str) -> str:
    normalized = " ".join(str(text).split()).strip().rstrip(". ")
    if not normalized:
        return ""
    if is_subjectless_action_fragment(normalized) or normalized[:1].islower():
        clause = evidence_fragment_to_clause(normalized)
        return f"That work has included {clause}."
    sentence = normalized[:1].upper() + normalized[1:]
    if not sentence.endswith("."):
        sentence += "."
    return sentence


def polish_evidence_clause(evidence_text: str) -> str:
    raw = " ".join(str(evidence_text).split()).strip()
    if not raw:
        return ""

    if is_dirty_cover_letter_text(raw):
        return ""
    if any(issue.rule != "subjectless_action_fragment" for issue in lint_cover_letter_text(raw)):
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
        "tags:",
        "tag:",
        "keywords:",
        "keyword:",
    )
    if any(marker in lowered for marker in blocked):
        return ""
    if re.search(r"\bor\b.+where supported by actual projects", lowered):
        return ""
    if "placeholder:" in lowered or "where verified" in lowered:
        return ""
    if " or " in lowered:
        tech_hits = sum(1 for token in ("c#", ".net", "asp.net", ".net core", "entity framework", "sql") if token in lowered)
        if tech_hits >= 2 and any(token in lowered for token in ("where verified", "where supported", "project history")):
            return ""
    if re.fullmatch(r"[a-z ]{2,35}:", lowered):
        return ""
    if any(
        lowered.startswith(prefix)
        for prefix in (
            "context:",
            "purpose:",
            "role:",
            "technologies:",
            "tags:",
            "tag:",
            "keywords:",
            "keyword:",
            "what was built:",
            "outcome:",
            "safe claims:",
            "claims to avoid:",
        )
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
    return evidence_fragment_to_clause(text[:260])


def polish_evidence_for_cover_letter(evidence_text: str) -> str:
    return polish_evidence_clause(evidence_text)


def is_safe_cover_letter_evidence_fragment(text: str, *, require_full_sentence: bool = False) -> bool:
    normalized = " ".join(str(text).split()).strip()
    if not normalized:
        return False
    if is_dirty_cover_letter_text(normalized):
        return False
    if lint_cover_letter_text(normalized):
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
        "tags:",
        "tag:",
        "keywords:",
        "keyword:",
    )
    if any(marker in lowered for marker in blocked):
        return False
    if re.fullmatch(r"[a-z ]{2,35}:", lowered):
        return False
    if require_full_sentence and is_subjectless_action_fragment(normalized):
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
- Do not let two nearby sentences in the same paragraph begin with "This".
- Avoid repeated noun stacking such as "development work", "hands-on development work", and "This work" in one paragraph.
- Keep the second paragraph anchored by either one concrete evidence card or one polished generic evidence sentence.
- Avoid repeating generic descriptors such as practical, reliable, operational, stable, maintainability, and business-critical.
- Prefer company-specific phrasing like "Forterra's platform environment" instead of "the team's platform environment".
- Keep the letter to 4 body paragraphs or fewer before the sign-off.

Required paragraph roles:
- Paragraph 1: Role fit only. Mention the role, company, and 2 to 4 core skills. Do not include detailed work-style claims.
- Paragraph 2: Evidence only. Use one concrete experience or curated evidence anchor.
- Paragraph 3: Motivation and working style. Explain interest in the role and how the candidate works.
- Paragraph 4: Closing only. Keep it short and invite discussion.

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
        and str(item.get("source_file") or "").strip() in APPLICANT_FACING_EVIDENCE_FILES
    ][:max_cards]

    if not evidence_cards:
        evidence_cards = [
            {
                "theme": "Fallback-safe evidence",
                "text": GENERIC_SAFE_EVIDENCE_SENTENCE,
                "source_file": "generic-safe-fallback",
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


def validate_cover_letter_brief(
    letter_brief: dict[str, Any],
    allow_generic_evidence: bool = False,
    app_dir: Path | None = None,
) -> None:
    prefix = "cover letter brief invalid"

    def fail(
        message: str,
        *,
        field_path: str | None = None,
        rule: str | None = None,
        offending_sentence: str | None = None,
        source_file: str | None = None,
        evidence_card_index: int | None = None,
    ) -> None:
        _write_cover_letter_brief_error(
            app_dir,
            error_message=message,
            field_path=field_path,
            rule=rule,
            offending_sentence=offending_sentence,
            source_file=source_file,
            evidence_card_index=evidence_card_index,
        )
        raise ProcessingError(message)

    def fail_issue(
        issue: CoverLetterQualityIssue,
        *,
        field_path: str | None = None,
        source_file: str | None = None,
        evidence_card_index: int | None = None,
    ) -> None:
        message = format_cover_letter_quality_issue(prefix, issue, field_path=field_path)
        fail(
            message,
            field_path=field_path,
            rule=issue.rule,
            offending_sentence=issue.sentence,
            source_file=source_file,
            evidence_card_index=evidence_card_index,
        )

    def validate_field(
        value: Any,
        *,
        field_path: str,
        source_file: str | None = None,
        evidence_card_index: int | None = None,
    ) -> None:
        text = str(value or "").strip()
        if not text:
            return
        lowered = text.casefold()
        if "minimum 2-3 years" in lowered:
            fail_issue(
                CoverLetterQualityIssue(
                    rule="internal_requirement",
                    message="Contains internal requirement language",
                    sentence=text,
                ),
                field_path=field_path,
                source_file=source_file,
                evidence_card_index=evidence_card_index,
            )
        if "todo" in lowered:
            fail_issue(
                CoverLetterQualityIssue(
                    rule="todo_placeholder",
                    message="Contains TODO placeholder",
                    sentence=text,
                ),
                field_path=field_path,
                source_file=source_file,
                evidence_card_index=evidence_card_index,
            )
        issues = lint_cover_letter_text(text)
        if field_path.endswith(".text") and field_path.startswith("evidence_cards["):
            issues = [issue for issue in issues if issue.rule != "subjectless_action_fragment"]
        if issues:
            fail_issue(
                issues[0],
                field_path=field_path,
                source_file=source_file,
                evidence_card_index=evidence_card_index,
            )
        if is_dirty_cover_letter_text(text):
            fail_issue(
                CoverLetterQualityIssue(
                    rule="dirty_scaffold_text",
                    message="Contains dirty scaffold/template text",
                    sentence=text,
                ),
                field_path=field_path,
                source_file=source_file,
                evidence_card_index=evidence_card_index,
            )

    role = str(letter_brief.get("role_title") or "").strip()
    company = str(letter_brief.get("company") or "").strip()
    candidate_name = str(letter_brief.get("candidate_name") or "").strip()
    if not role:
        fail(f"{prefix}: role_title is required", field_path="role_title", rule="required_field")
    if not company:
        fail(f"{prefix}: company is required", field_path="company", rule="required_field")
    if candidate_name != DEFAULT_CANDIDATE_NAME:
        fail(f"{prefix}: candidate_name mismatch", field_path="candidate_name", rule="identity_mismatch")

    validate_field(letter_brief.get("role_title"), field_path="role_title")
    validate_field(letter_brief.get("company"), field_path="company")
    validate_field(letter_brief.get("location"), field_path="location")

    for index, item in enumerate(letter_brief.get("matched_skills", [])):
        validate_field(item, field_path=f"matched_skills[{index}]")
    for index, item in enumerate(letter_brief.get("safe_resume_keywords", [])):
        validate_field(item, field_path=f"safe_resume_keywords[{index}]")
    for index, item in enumerate(letter_brief.get("reasons_to_apply", [])):
        validate_field(item, field_path=f"reasons_to_apply[{index}]")

    validate_field(letter_brief.get("suggested_resume_angle"), field_path="suggested_resume_angle")
    validate_field(letter_brief.get("cover_letter_angle"), field_path="cover_letter_angle")

    for index, item in enumerate(letter_brief.get("safe_enterprise_themes", [])):
        validate_field(item, field_path=f"safe_enterprise_themes[{index}]")

    cards = [card for card in letter_brief.get("evidence_cards", []) if isinstance(card, dict)]
    for card_index, card in enumerate(cards):
        source_file = str(card.get("source_file") or "unknown").strip()
        validate_field(
            card.get("theme"),
            field_path=f"evidence_cards[{card_index}].theme",
            source_file=source_file,
            evidence_card_index=card_index,
        )
        validate_field(
            card.get("text"),
            field_path=f"evidence_cards[{card_index}].text",
            source_file=source_file,
            evidence_card_index=card_index,
        )
        validate_field(
            card.get("claim_boundary"),
            field_path=f"evidence_cards[{card_index}].claim_boundary",
            source_file=source_file,
            evidence_card_index=card_index,
        )

    serialized = json.dumps(letter_brief, ensure_ascii=False)
    serialized_issues = lint_cover_letter_text(serialized)
    if serialized_issues:
        fail_issue(serialized_issues[0], field_path="letter_brief(serialized)")

    clean_cards = [
        card
        for card in cards
        if not is_dirty_cover_letter_text(str(card.get("text") or ""))
        and not [
            issue
            for issue in lint_cover_letter_text(str(card.get("text") or ""))
            if issue.rule != "subjectless_action_fragment"
        ]
    ]
    if not clean_cards and not allow_generic_evidence:
        fail(f"{prefix}: no clean evidence cards", field_path="evidence_cards", rule="no_clean_evidence_cards")


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
- Paragraph 1 focuses on role fit only
- Paragraph 2 contains the concrete evidence
- Paragraph 3 focuses on motivation and working style without repeating the evidence sentence
- Paragraph 4 is a short closing invitation
- second paragraph contains one concrete evidence anchor or one polished generic evidence sentence
- does not repeat nearby sentence openings with "This"
- avoids repeated noun stacking like development work / this work
- avoids repeated generic descriptors like practical, reliable, operational, stable, maintainability, or business-critical
- avoids closing filler such as "I am available to share concrete examples..."
- prefers company-specific phrasing over "the team's platform environment"
- avoids adjacent paragraph overlap on testing / handoff / workflow / validation / maintainable concepts
- stays within 4 body paragraphs before the sign-off
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
- avoid repeated "This" openings in the same paragraph
- avoid repeated noun stacking like development work / this work
- avoid repeated generic descriptors like practical, reliable, operational, stable, maintainability, or business-critical
- prefer company-specific phrasing over "the team's platform environment"
- keep the letter to 4 body paragraphs or fewer before the sign-off
- Paragraph 1: role fit only, with role, company, and 2 to 4 core skills
- Paragraph 2: concrete evidence only
- Paragraph 3: motivation and working style only
- Paragraph 4: short closing invitation
- avoid repeating generic testing / handoff / workflow / validation concepts across adjacent paragraphs

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
    role_repair_guidance = ""
    lowered_error = validation_error.casefold()
    if "paragraph role rule" in lowered_error or "paragraph_role_" in lowered_error:
        role_repair_guidance = (
            "- Do not add new claims.\n"
            "- Make paragraph 1 shorter and focused on role fit.\n"
            "- Move work evidence to paragraph 2.\n"
            "- Keep paragraph 3 focused on interest and working style.\n"
            "- Remove repeated concepts instead of paraphrasing them.\n"
        )
    return f"""Rewrite this cover letter so it is safe and submission-ready.

Rules:
- Return only the corrected cover letter.
- The previous output failed because of this exact sentence-level issue.
- Start with: Dear Hiring Manager,
- End with:
    Best regards,

    Vincent Morrill
- Remove or rewrite the offending sentence.
- Remove all scaffold, TODO, template, verification, and claim-boundary language.
- Use only factual content from the Letter Brief evidence_cards.
- Do not use phrases like where verified, where supported, Placeholder, TODO, or or ... where verified.
- If a technology is not safe to claim, omit it rather than writing verification language.
- Keep the letter factual, concise, and sendable.
- Do not let two nearby sentences in the same paragraph begin with "This".
- Remove repeated noun stacking like development work / hands-on development work / this work.
- Remove repeated generic descriptors such as practical, reliable, operational, stable, maintainability, and business-critical.
- Remove closing filler like "I am available to share concrete examples..." when the paragraph already invites discussion.
- Prefer company-specific phrasing over "the team's platform environment".
- Keep the second paragraph anchored by one concrete evidence card or one polished generic evidence sentence.
- Keep the letter to 4 body paragraphs or fewer before the sign-off.
- Use this paragraph structure:
    - Paragraph 1: role fit only
    - Paragraph 2: evidence only
    - Paragraph 3: motivation and working style
    - Paragraph 4: short closing invitation
{role_repair_guidance}

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
        if str(item.get("text") or "").strip()
        and str(item.get("source_file") or "") == "cover_letter_evidence.md"
        and not is_dirty_cover_letter_text(str(item.get("text") or ""))
    ]
    polished_evidence = [polish_evidence_clause(item) for item in evidence_texts]
    polished_evidence = [
        item
        for item in polished_evidence
        if is_safe_cover_letter_evidence_fragment(item) and not lint_cover_letter_text(item)
    ]

    if polished_evidence:
        second_paragraph = (
            "One example is my work on "
            f"{polished_evidence[0]}, which gave me practice translating business rules into dependable application behavior. "
            "That experience also required validation through tests, clear handoff to teammates, and maintainable changes that fit day-to-day use."
        )
    else:
        second_paragraph = evidence_fragment_to_sentence(
            "feature contributions, testing-focused improvements, and workflow refinements that required careful validation, clear handoff to teammates, and maintainable changes"
        )

    letter = (
        "Dear Hiring Manager,\n\n"
        f"I am applying for the {role_title} position at {company}. My background in {stack} aligns with {company}'s focus on application services and internal platforms.\n\n"
        "In recent development work, I have contributed to internal and enterprise-grade systems by implementing features, writing unit tests, improving workflow behavior, and working through pull-request-based development. "
        f"{second_paragraph}\n\n"
        f"What interests me about this role is the mix of software delivery, stakeholder support, and steady improvement of business applications. I work best by clarifying requirements, communicating directly with teammates, and keeping implementation choices understandable within {company}'s platform environment.\n\n"
        f"Thank you for your consideration. I would welcome a conversation about how my C#/.NET application experience can support {company}'s team.\n\n"
        "Best regards,\n\n"
        "Vincent Morrill"
    )
    validate_generated_cover_letter(letter)
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
    app_dir: Path | None = None,
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
    evidence_mode = "generic_safe_fallback"
    applicant_facing_evidence_sources: list[str] = []
    reference_context_sources = list(REFERENCE_CONTEXT_FILES)

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
    if settings is not None:
        try:
            applicant_facing_evidence_sources = list_existing_profile_evidence_sources(
                settings,
                APPLICANT_FACING_EVIDENCE_FILES,
            )
        except Exception:
            applicant_facing_evidence_sources = []

        if applicant_facing_evidence_sources:
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
                    cards = search_applicant_facing_evidence(settings, query, themes, max_results=max_evidence_cards)
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

    evidence_cards = [
        card for card in evidence_cards if str(card.get("source_file") or "") in APPLICANT_FACING_EVIDENCE_FILES
    ]
    evidence_mode = "curated_only" if evidence_cards and all(str(card.get("source_file") or "") == "cover_letter_evidence.md" for card in evidence_cards) else "generic_safe_fallback"

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
        validate_cover_letter_brief(letter_brief, app_dir=app_dir)
    except ProcessingError:
        repaired_cards = []
        for card in letter_brief.get("evidence_cards", []):
            if not isinstance(card, dict):
                continue
            text = str(card.get("text") or "").strip()
            if not text or is_dirty_cover_letter_text(text):
                maybe_record_dirty(text)
                continue
            if str(card.get("source_file") or "") not in APPLICANT_FACING_EVIDENCE_FILES:
                continue
            repaired_cards.append(card)
        if not repaired_cards:
            repaired_cards = [
                {
                    "theme": "Fallback-safe evidence",
                    "text": GENERIC_SAFE_EVIDENCE_SENTENCE,
                    "source_file": "generic-safe-fallback",
                    "claim_boundary": "Do not overclaim ownership or unverified platform expertise.",
                }
            ]
            evidence_mode = "generic_safe_fallback"
        letter_brief["evidence_cards"] = repaired_cards[:max_evidence_cards]
        validate_cover_letter_brief(letter_brief, allow_generic_evidence=True, app_dir=app_dir)

    fallback_content = sanitize_generated_cover_letter(build_fallback_cover_letter(letter_brief))
    fallback_note = None
    if "One example is" not in fallback_content:
        fallback_note = "No clean evidence cards were available."
    expected_queries = max_model_calls
    tool_access = {
        "enabled": settings is not None,
        "allowed_sources": list(APPLICANT_FACING_EVIDENCE_FILES),
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
            evidence_mode=evidence_mode,
            applicant_facing_evidence_sources=applicant_facing_evidence_sources,
            reference_context_sources=reference_context_sources,
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
            if repair_passes > 0:
                for attempt in range(1, repair_passes + 1):
                    if not can_call_model():
                        break
                    repair_attempted = True
                    record_query(f"repair-pass-{attempt}")
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
                    if not repaired:
                        continue
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
                            evidence_mode=evidence_mode,
                            applicant_facing_evidence_sources=applicant_facing_evidence_sources,
                            reference_context_sources=reference_context_sources,
                            tool_access=tool_access,
                            repair_attempted=repair_attempted,
                            repair_successful=repair_successful,
                            fallback_reason=None,
                            validation_error=None,
                        )
                    except ProcessingError as repair_exc:
                        validation_error_clean = sanitize_cover_letter_reason(str(repair_exc))
                        continue
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
            evidence_mode=evidence_mode,
            applicant_facing_evidence_sources=applicant_facing_evidence_sources,
            reference_context_sources=reference_context_sources,
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
