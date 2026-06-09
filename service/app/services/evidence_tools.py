"""Allowlisted evidence retrieval tools for cover letter generation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_job_search.model_provider import ModelRequest
from ai_job_search.model_provider import ModelProvider
from ai_job_search.model_provider import ModelProviderError

from app.config import Settings


APPLICANT_FACING_EVIDENCE_FILES = (
    "cover_letter_evidence.md",
)

REFERENCE_CONTEXT_FILES = (
    "resume_facts.md",
    "project_inventory.md",
    "experience_bullets.md",
    "skills_inventory.md",
    "experience_timeline.md",
    "education.md",
    "disallowed_claims.md",
    "generation-constraints.md",
)

ALLOWED_PROFILE_EVIDENCE_FILES = APPLICANT_FACING_EVIDENCE_FILES + REFERENCE_CONTEXT_FILES

BOUNDARY_ONLY_FILES = {"disallowed_claims.md", "generation-constraints.md"}

SCAFFOLD_PHRASE_MARKERS = (
    "add verified academic",
    "project template",
    "known technical areas",
    "candidate project leads",
    "where supported by actual projects",
    "safe themes to verify",
    "verify and expand",
    "to verify",
    "missing details",
    "claims to avoid",
    "do not claim",
    "unless verified",
    "exact role titles",
    "exact dates",
    "project names and outcomes",
    "technologies used in each role",
    "any quantified achievements",
    "supported by actual use",
    "familiarity unless",
    "claim boundaries",
    "manual review notes",
    "project name",
    "context:",
    "purpose:",
    "role:",
    "technologies:",
    "what was built:",
    "outcome:",
    "safe claims:",
    "claims to avoid:",
    "add only projects",
    "tags:",
    "tag:",
    "keywords:",
    "keyword:",
)

TEMPLATE_ONLY_HEADINGS = {
    "project template",
    "known technical areas to map to projects",
    "candidate project leads to verify",
    "manual review notes",
    "missing details to fill manually",
    "technical skills to verify",
}

TEMPLATE_LABEL_PREFIXES = (
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
    "project name",
)

DIRTY_EVIDENCE_MARKERS = (
    "todo",
    "tbd",
    "fixme",
    "relevant coursework or projects",
    "placeholder:",
    "placeholder",
    "add verified",
    "project template",
    "known technical areas",
    "candidate project leads",
    "manual review notes",
    "missing details",
    "technical skills to verify",
    "where supported by actual projects",
    "where verified",
    "where verified by project history",
    "project history",
    "safe themes to verify",
    "verify and expand",
    "unless verified",
    "claims to avoid",
    "do not claim",
    "safe claims",
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
)

ACCOMPLISHMENT_MARKERS = (
    "contributed",
    "implemented",
    "wrote",
    "added",
    "modified",
    "improved",
    "tested",
    "unit test",
    "pull request",
    "debugging",
    "documentation",
    "workflow",
    "business rules",
    "api",
    "sql",
    ".net",
    "c#",
)

SKILL_LIST_TOKENS = (
    "c#",
    ".net",
    "asp.net",
    "sql",
    "api",
    "angular",
    "typescript",
    "javascript",
    "python",
    "azure",
    "cloud",
)

LINT_TECH_SKILL_TOKENS = (
    "c#",
    ".net",
    "asp.net",
    ".net core",
    "entity framework",
    "sql",
    "azure",
    "power platform",
    "microsoft 365",
    "iam",
)


@dataclass(frozen=True)
class CoverLetterQualityIssue:
    rule: str
    message: str
    sentence: str


def _profile_dir(settings: Settings) -> Path:
    return settings.app_data_dir / "profile"


def _is_unsafe_query(value: str) -> bool:
    lowered = value.strip().lower()
    if not lowered:
        return True
    return any(marker in lowered for marker in ("..", "/", "\\", ":"))


def _read_allowlisted_file(settings: Settings, filename: str) -> str:
    if filename not in ALLOWED_PROFILE_EVIDENCE_FILES:
        return ""
    path = _profile_dir(settings) / filename
    try:
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _extract_headings(text: str, limit: int = 6) -> list[str]:
    headings: list[str] = []
    for line in text.splitlines():
        item = line.strip()
        if item.startswith("#"):
            headings.append(item.lstrip("#").strip())
        if len(headings) >= limit:
            break
    return headings


def list_profile_evidence_sources(settings: Settings) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for filename in ALLOWED_PROFILE_EVIDENCE_FILES:
        path = _profile_dir(settings) / filename
        exists = path.is_file()
        size = 0
        headings: list[str] = []
        if exists:
            try:
                content = path.read_text(encoding="utf-8")
                size = len(content.encode("utf-8"))
                headings = _extract_headings(content)
            except OSError:
                exists = False
                size = 0
                headings = []
        sources.append(
            {
                "source_file": filename,
                "exists": exists,
                "size_bytes": size,
                "headings": headings,
            }
        )
    return sources


def list_existing_profile_evidence_sources(settings: Settings, source_files: tuple[str, ...]) -> list[str]:
    existing: list[str] = []
    for filename in source_files:
        path = _profile_dir(settings) / filename
        try:
            if path.is_file() and path.read_text(encoding="utf-8").strip():
                existing.append(filename)
        except OSError:
            continue
    return existing


def _split_snippets(text: str) -> list[str]:
    def normalize_heading(value: str) -> str:
        return " ".join(value.casefold().split())

    candidates: list[str] = []
    in_template_only_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            heading_text = line.lstrip("#").strip()
            in_template_only_section = normalize_heading(heading_text) in TEMPLATE_ONLY_HEADINGS
            continue
        if in_template_only_section:
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        if len(line) < 25:
            continue
        candidates.append(line)

    if not candidates:
        paragraphs = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
        candidates.extend(paragraphs)
    return candidates


def _clip_text(text: str, limit: int = 300) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def split_cover_letter_sentences(text: str) -> list[str]:
    normalized = " ".join(str(text).split())
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+", normalized)
    sentences = [part.strip() for part in parts if part.strip()]
    return sentences or [normalized]


def lint_cover_letter_sentence(sentence: str) -> list[CoverLetterQualityIssue]:
    text = " ".join(str(sentence).split()).strip()
    if not text:
        return []
    lowered = text.casefold()
    issues: list[CoverLetterQualityIssue] = []

    def add(rule: str, message: str) -> None:
        issues.append(CoverLetterQualityIssue(rule=rule, message=message, sentence=text))

    banned_markers = (
        "todo",
        "tbd",
        "fixme",
        "placeholder:",
        "placeholder",
        "where verified",
        "where verified by project history",
        "where supported",
        "where supported by actual projects",
        "unless verified",
        "safe claims",
        "claims to avoid",
        "do not claim",
        "manual review notes",
        "relevant coursework or projects",
        "add verified",
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
    if any(marker in lowered for marker in banned_markers):
        add("verification_scaffold", "Sentence includes scaffold/verification language")

    if re.search(r":\s*todo\b", lowered):
        add("todo_placeholder", "Sentence includes TODO placeholder")

    starts_with_blocks = (
        "examples include placeholder",
        "examples include relevant",
        "examples include context:",
        "examples include purpose:",
        "examples include role:",
        "examples include technologies:",
        "examples include tags:",
        "examples include tag:",
        "examples include keywords:",
        "sql or",
        "c#, .net",
    )
    if any(lowered.startswith(prefix) for prefix in starts_with_blocks):
        add("choppy_scaffold_fragment", "Sentence starts with a scaffold fragment")

    if re.search(r"\b(tags?|keywords?):\s*[a-z0-9#.,+\-/ ]{3,}", lowered):
        add("metadata_label_fragment", "Sentence includes metadata/label text instead of applicant-facing prose")

    if ". sql or" in lowered or "and sql and" in lowered:
        add("grammar_fragment", "Sentence contains a broken grammar fragment")

    if " or " in lowered:
        tech_hits = sum(1 for token in LINT_TECH_SKILL_TOKENS if token in lowered)
        has_verification = any(token in lowered for token in ("where verified", "where supported", "project history", "actual projects"))
        if tech_hits >= 2 and has_verification:
            add("skill_list_verification_pattern", "Sentence mixes skill-list disjunction with verification scaffold")

    meta_markers = (
        "provided letter brief",
        "based on the provided",
        "as a language model",
        "this cover letter",
        "the candidate should",
    )
    if any(marker in lowered for marker in meta_markers):
        add("meta_instruction", "Sentence contains meta/instruction language")

    return issues


def lint_cover_letter_text(text: str) -> list[CoverLetterQualityIssue]:
    issues: list[CoverLetterQualityIssue] = []
    for sentence in split_cover_letter_sentences(text):
        issues.extend(lint_cover_letter_sentence(sentence))
    return issues


def is_dirty_cover_letter_text(text: str) -> bool:
    normalized = " ".join(str(text).split()).strip()
    if not normalized:
        return True

    lowered = normalized.casefold()
    if any(marker in lowered for marker in DIRTY_EVIDENCE_MARKERS):
        return True
    if lint_cover_letter_text(normalized):
        return True
    if re.search(r":\s*todo\b", lowered):
        return True
    if re.fullmatch(r"[a-z ]{2,40}:", lowered):
        return True
    if lowered.startswith("add verified"):
        return True
    if any(lowered.startswith(prefix) for prefix in TEMPLATE_LABEL_PREFIXES):
        return True
    if any(token in lowered for token in ("template", "placeholder", "instruction")) and not any(
        marker in lowered for marker in ACCOMPLISHMENT_MARKERS
    ):
        return True
    return False


def is_applicant_facing_evidence(text: str) -> bool:
    raw = " ".join(text.split()).strip()
    if not raw:
        return False
    if is_dirty_cover_letter_text(raw):
        return False

    lowered = " ".join(text.casefold().split())
    if not lowered:
        return False
    if any(marker in lowered for marker in SCAFFOLD_PHRASE_MARKERS):
        return False
    if lint_cover_letter_text(raw):
        return False

    accomplishment_present = any(marker in lowered for marker in ACCOMPLISHMENT_MARKERS)
    instructionish = any(
        marker in lowered
        for marker in (
            "add only",
            "to verify",
            "missing details",
            "manual review",
            "candidate project leads",
            "safe themes",
            "project template",
        )
    )
    if instructionish and not accomplishment_present:
        return False
    return True


def _looks_like_skill_list_only(snippet: str) -> bool:
    lowered = snippet.casefold()
    alpha_words = re.findall(r"[a-zA-Z][a-zA-Z+#.\-]+", lowered)
    comma_count = snippet.count(",")
    skill_hits = sum(1 for token in SKILL_LIST_TOKENS if token in lowered)
    has_action = any(marker in lowered for marker in ACCOMPLISHMENT_MARKERS)
    return bool(skill_hits >= 3 and comma_count >= 2 and len(alpha_words) <= 18 and not has_action)


def _quality_bonus(snippet: str, source_file: str) -> int:
    lowered = snippet.casefold()
    bonus = 0
    for marker in ACCOMPLISHMENT_MARKERS:
        if marker in lowered:
            bonus += 1
    if _looks_like_skill_list_only(snippet):
        bonus -= 3
    if source_file == "cover_letter_evidence.md":
        bonus += 5
    return bonus


def _sanitize_claim_boundary_line(line: str) -> str:
    text = re.sub(r"[*_`]+", "", str(line).strip(" -\t")).strip()
    if not text or text.startswith("#"):
        return ""

    lowered = text.casefold()
    if lowered.startswith("use instead:"):
        return ""
    if lowered.startswith("do not claim the candidate lacks enterprise application experience"):
        return ""

    if lowered.startswith("do not claim "):
        claim = text[len("Do not claim ") :].rstrip(".")
        claim = re.sub(r"\s+unless verified$", "", claim, flags=re.IGNORECASE)
        claim = re.sub(r"\s+not explicitly listed$", " not explicitly listed", claim, flags=re.IGNORECASE)
        return f"Avoid claiming {claim}."

    if "may be described only at the level supported by actual use" in lowered:
        subject = text.split(" may be described", 1)[0].strip()
        return f"Keep {subject} references conservative and limited to direct hands-on use."

    if "should be described as exposure unless verified as hands-on ownership" in lowered:
        subject = text.split(" should be described", 1)[0].strip()
        return f"Frame {subject} as exposure rather than ownership."

    if "should be described as familiarity unless project evidence supports more" in lowered:
        subject = text.split(" should be described", 1)[0].strip()
        return f"Frame {subject} as familiarity rather than ownership."

    if "claims require explicit evidence before use" in lowered:
        subject = text.split(" claims require", 1)[0].strip()
        return f"Avoid making {subject} ownership claims without direct evidence."

    return text


def _claim_boundary_for_snippet(disallowed_text: str, snippet: str, theme: str) -> str:
    boundary_default = "Do not overclaim ownership, senior architecture authority, cloud ownership, certifications, or unverified platform expertise."
    lowered_snippet = snippet.casefold()
    lowered_theme = theme.casefold()
    lines = [line.strip() for line in disallowed_text.splitlines() if line.strip()]
    matches: list[str] = []
    for line in lines:
        if line.startswith("#"):
            continue
        lowered_line = line.casefold()
        tokens = re.findall(r"[a-zA-Z0-9#.+-]+", lowered_line)
        if any(token in lowered_snippet or token in lowered_theme for token in tokens[:8] if len(token) > 3):
            sanitized = _sanitize_claim_boundary_line(line)
            if sanitized and sanitized not in matches:
                matches.append(sanitized)
        if len(matches) >= 2:
            break
    if not matches:
        return boundary_default
    return " ".join(matches)[:220]


def search_profile_evidence(
    settings: Settings,
    query: str,
    themes: list[str],
    max_results: int = 6,
    source_files: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    if _is_unsafe_query(query):
        raise ValueError("unsafe evidence query")

    normalized_query = " ".join(query.split())
    max_cards = max(1, min(max_results, 10))

    disallowed_text = _read_allowlisted_file(settings, "disallowed_claims.md")

    corpus: list[tuple[str, str]] = []
    candidate_files = source_files or ALLOWED_PROFILE_EVIDENCE_FILES
    for filename in candidate_files:
        if filename in BOUNDARY_ONLY_FILES:
            continue
        content = _read_allowlisted_file(settings, filename)
        if not content:
            continue
        for snippet in _split_snippets(content):
            corpus.append((filename, snippet))

    query_tokens = {
        token
        for token in re.findall(r"[a-zA-Z0-9#.+-]+", normalized_query.casefold())
        if len(token) > 2
    }

    results: list[dict[str, Any]] = []
    for filename, snippet in corpus:
        if not is_applicant_facing_evidence(snippet):
            continue
        lowered = snippet.casefold()
        token_hits = sum(1 for token in query_tokens if token in lowered)
        if token_hits == 0:
            continue

        score = token_hits + _quality_bonus(snippet, filename)
        confidence = "high" if score >= 6 else "medium" if score >= 4 else "low"
        theme = themes[0] if themes else "Evidence"
        for candidate in themes:
            candidate_tokens = [tok for tok in re.findall(r"[a-zA-Z0-9#.+-]+", candidate.casefold()) if len(tok) > 2]
            if any(tok in lowered for tok in candidate_tokens):
                theme = candidate
                break

        results.append(
            {
                "theme": theme,
                "text": _clip_text(snippet, limit=300),
                "source_file": filename,
                "confidence": confidence,
                "claim_boundary": _claim_boundary_for_snippet(disallowed_text, snippet, theme),
                "_score": score,
            }
        )

    results.sort(key=lambda item: (item.get("source_file") != "cover_letter_evidence.md", -int(item.get("_score", 0))))
    trimmed = results[:max_cards]
    for item in trimmed:
        item.pop("_score", None)
    return trimmed


def search_applicant_facing_evidence(
    settings: Settings,
    query: str,
    themes: list[str],
    max_results: int = 6,
) -> list[dict[str, Any]]:
    return search_profile_evidence(
        settings,
        query,
        themes,
        max_results=max_results,
        source_files=APPLICANT_FACING_EVIDENCE_FILES,
    )


def build_evidence_queries(job: dict[str, Any], fit: dict[str, Any]) -> list[str]:
    title = str(job.get("title") or "").strip()
    company = str(job.get("company") or "").strip()
    matched = [str(item).strip() for item in fit.get("matched_skills", []) if str(item).strip()]
    seeds = [
        "C# .NET SQL enterprise application evidence",
        "internal applications stakeholder workflow improvement evidence",
        "insurance benefits software quoting workflows evidence",
        "unit testing pull-request-based development evidence",
        "application services maintainability debugging collaboration evidence",
    ]
    if title or company:
        seeds.insert(0, f"{title} {company} role relevant evidence".strip())
    if matched:
        seeds.append(" ".join(matched[:4]) + " verified project evidence")

    deduped: list[str] = []
    for query in seeds:
        normalized = " ".join(query.split())
        if normalized and normalized not in deduped and not _is_unsafe_query(normalized):
            deduped.append(normalized)
    return deduped[:10]


def plan_cover_letter_evidence_queries(
    job: dict[str, Any],
    fit: dict[str, Any],
    provider: ModelProvider,
    max_queries: int = 10,
) -> list[str]:
    deterministic = build_evidence_queries(job, fit)[:max_queries]
    request = ModelRequest(
        system_prompt=(
            "You produce safe evidence-search query plans. "
            "Return JSON only with {\"queries\":[...]} and do not include paths or filenames."
        ),
        user_prompt=json.dumps(
            {
                "job_title": job.get("title"),
                "company": job.get("company"),
                "matched_skills": fit.get("matched_skills", []),
                "reasons_to_apply": fit.get("reasons_to_apply", []),
                "constraints": [
                    "At most 10 queries",
                    "No path separators",
                    "No filenames",
                    "Focus on concrete candidate evidence",
                ],
                "output_shape": {"queries": ["query 1", "query 2"]},
            },
            ensure_ascii=False,
            indent=2,
        ),
        temperature=0,
        max_tokens=260,
        response_format="text",
    )
    try:
        response = provider.complete(request)
        parsed = json.loads(response.text)
        queries = parsed.get("queries") if isinstance(parsed, dict) else None
        if not isinstance(queries, list):
            return deterministic
        cleaned: list[str] = []
        for item in queries:
            query = " ".join(str(item).split())
            if not query or _is_unsafe_query(query):
                continue
            if query not in cleaned:
                cleaned.append(query)
            if len(cleaned) >= max_queries:
                break
        return (cleaned or deterministic)[:max_queries]
    except (ModelProviderError, json.JSONDecodeError, OSError, ValueError, TypeError):
        return deterministic
