"""Allowlisted evidence retrieval tools for cover letter generation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ai_job_search.model_provider import ModelRequest
from ai_job_search.model_provider import ModelProvider
from ai_job_search.model_provider import ModelProviderError

from app.config import Settings


ALLOWED_PROFILE_EVIDENCE_FILES = (
    "cover_letter_evidence.md",
    "resume_facts.md",
    "project_inventory.md",
    "experience_bullets.md",
    "skills_inventory.md",
    "experience_timeline.md",
    "education.md",
    "disallowed_claims.md",
    "generation-constraints.md",
)

BOUNDARY_ONLY_FILES = {"disallowed_claims.md", "generation-constraints.md"}


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


def _split_snippets(text: str) -> list[str]:
    candidates: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
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


def _claim_boundary_for_snippet(disallowed_text: str, snippet: str, theme: str) -> str:
    boundary_default = "Do not overclaim ownership, senior architecture authority, cloud ownership, certifications, or unverified platform expertise."
    lowered_snippet = snippet.casefold()
    lowered_theme = theme.casefold()
    lines = [line.strip(" -\t") for line in disallowed_text.splitlines() if line.strip()]
    matches: list[str] = []
    for line in lines:
        lowered_line = line.casefold()
        tokens = re.findall(r"[a-zA-Z0-9#.+-]+", lowered_line)
        if any(token in lowered_snippet or token in lowered_theme for token in tokens[:8] if len(token) > 3):
            matches.append(line)
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
) -> list[dict[str, Any]]:
    if _is_unsafe_query(query):
        raise ValueError("unsafe evidence query")

    normalized_query = " ".join(query.split())
    max_cards = max(1, min(max_results, 6))

    disallowed_text = _read_allowlisted_file(settings, "disallowed_claims.md")

    corpus: list[tuple[str, str]] = []
    for filename in ALLOWED_PROFILE_EVIDENCE_FILES:
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
        lowered = snippet.casefold()
        token_hits = sum(1 for token in query_tokens if token in lowered)
        if token_hits == 0:
            continue

        proof_bonus = 0
        if any(marker in lowered for marker in ("contributed", "implemented", "unit", "workflow", "pull-request", "debug")):
            proof_bonus += 2
        if filename == "cover_letter_evidence.md":
            proof_bonus += 3

        score = token_hits + proof_bonus
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
    return deduped[:5]


def plan_cover_letter_evidence_queries(
    job: dict[str, Any],
    fit: dict[str, Any],
    provider: ModelProvider,
) -> list[str]:
    deterministic = build_evidence_queries(job, fit)
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
                    "At most 5 queries",
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
            if len(cleaned) >= 5:
                break
        return cleaned or deterministic
    except (ModelProviderError, json.JSONDecodeError, OSError, ValueError, TypeError):
        return deterministic
