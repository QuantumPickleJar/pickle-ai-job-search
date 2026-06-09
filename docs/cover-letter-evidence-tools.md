# Cover Letter Evidence Tools

This document describes the scoped, read-only evidence tools used by cover letter generation.

## Why These Tools Exist

The cover letter flow needs concrete, candidate-specific evidence without exposing broad filesystem access to the model.

The model can suggest evidence queries, but the service executes all file access through strict allowlisted tools.

## Allowed Sources

Evidence tools read only from `settings.app_data_dir/profile` and only these files:
- `cover_letter_evidence.md`
- `resume_facts.md`
- `project_inventory.md`
- `experience_bullets.md`
- `skills_inventory.md`
- `experience_timeline.md`
- `education.md`
- `disallowed_claims.md`
- `generation-constraints.md`

## Blocked Sources

The evidence tools never read:
- `.env`
- Docker files
- service or repository source code
- generated application folders
- task JSON files
- logs
- arbitrary model-supplied paths
- traversal/path-like query content such as `../`, `/`, `\\`, or drive prefixes

## Tool Functions

- `list_profile_evidence_sources(settings)` returns metadata only.
- `search_profile_evidence(settings, query, themes, max_results)` returns short evidence cards.
- `build_evidence_queries(job, fit)` provides deterministic search queries.
- `plan_cover_letter_evidence_queries(job, fit, provider)` optionally asks the model for JSON-only query proposals; invalid output falls back to deterministic queries.

## Evidence Card Selection

Selection prioritizes:
1. `cover_letter_evidence.md` snippets when relevant
2. concrete proof points (feature work, testing, workflow impact)
3. snippets matching role-relevant themes

`disallowed_claims.md` is not used as positive evidence. It is used only to attach claim boundaries to evidence cards.

## Adding cover_letter_evidence.md

Create `profile/cover_letter_evidence.md` with concise, factual bullets describing verified contribution-level evidence.

Recommended bullet style:
- contribution verb
- concrete context (system/workflow)
- verified technology
- no inflated ownership claims

## Why This Is Safer Than Broad Filesystem Access

This design narrows the model's evidence surface to curated profile documents and controlled snippets.
It prevents accidental leakage from unrelated project files, secrets, generated artifacts, or operational logs while still improving cover letter quality with concrete evidence.
