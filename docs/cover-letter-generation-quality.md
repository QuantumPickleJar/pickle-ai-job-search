# Cover Letter Generation Quality

This document explains the safety and quality controls used by the cover letter generation pipeline.

## Safe Letter Brief

The service now builds a deterministic `Letter Brief` before generation.

The brief contains only applicant-facing-safe content:
- role title
- company
- location only when natural and useful
- candidate name and email
- matched skills
- safe resume keywords
- reasons to apply
- suggested resume angle
- cover letter angle
- curated profile context
- safe enterprise/application-development themes

The brief explicitly excludes internal-only analysis context:
- raw `missing_skills`
- raw `risks`
- raw `questions_to_answer_before_applying`
- raw `cover-letter-notes.md`
- requirement-copy phrases such as "Minimum 2-3 years"
- self-disqualifying platform requirement strings as candidate prose

## Multi-Pass Generation

When `COVER_LETTER_REVIEW_PASSES=true` (default), generation uses three model calls:
1. Draft pass from the Letter Brief
2. Critique pass against a quality rubric
3. Final rewrite pass using brief + draft + critique

If review passes are disabled, generation falls back to a single brief-based pass.

Pipeline order:
1. Retrieve evidence queries and evidence snippets
2. Reject dirty/scaffold/TODO evidence with sentence-level lint checks
3. Build Letter Brief
4. Validate Letter Brief before draft generation
5. Draft pass
6. Critique pass
7. Final rewrite pass
8. Validate final
9. Repair loop (`repair-pass-1`, `repair-pass-2`, ...) when final fails validation
10. Validate each repaired output
11. Deterministic fallback only when final and all configured repair passes fail

Generation provenance is persisted beside the letter in cover-letter.meta.json.
The source field is one of:
- model-final
- model-single-pass
- deterministic-fallback

The metadata file also stores:
- review_passes_enabled
- model_query_count_expected
- model_query_count_actual
- evidence_query_count_actual
- dirty_evidence_rejected_count
- evidence_cards_rejected_sample
- repair_attempted
- repair_successful
- fallback_reason when fallback is used
- validation_error when model output fails validation

Budget defaults:
- COVER_LETTER_MAX_EVIDENCE_QUERIES=10
- COVER_LETTER_MAX_EVIDENCE_CARDS=10
- COVER_LETTER_MAX_MODEL_CALLS=10
- COVER_LETTER_REPAIR_PASSES=2

## Validation Rules

Generated output is sanitized and then validated.

Validation enforces:
- starts with `Dear Hiring Manager,`
- includes `Best regards,`
- includes `Vincent Morrill`
- blocks known unsafe phrases and placeholders
- blocks sentence-level quality failures and reports rule + offending snippet
- blocks requirement-copy language (for example: "minimum 2-3 years")
- blocks awkward location text (`in Remote`)
- blocks generic filler (`ideal candidate`, `robust solutions`, `seamlessly integrate`)
- blocks JSON/code-fence output
- blocks TODO/TBD/FIXME and scaffold/template markers
- keeps word count within an acceptable range

## Deterministic Fallback

If final model output is unsafe or too weak, the service does not save it.

Instead, it writes a deterministic, polished fallback letter built from the safe Letter Brief.
The fallback is designed to be review-ready, while avoiding:
- unsupported claims
- raw missing skills
- copied requirements
- self-disqualifying language
- unsupported Azure/Microsoft 365/Power Platform/IAM statements

To check whether a generated letter used fallback, inspect cover-letter.meta.json and read:
- source
- fallback_reason
- evidence_cards_used (includes source_file)
- tool_access.allowed_sources
- repair_attempted / repair_successful
- evidence_query_count_actual / dirty_evidence_rejected_count

## Evidence Cards

The Letter Brief includes evidence_cards selected from curated profile context.

Selection is deterministic and evidence-only:
- BizLink mentions map to insurance quoting workflow evidence
- UWO, portal, RoStar, and university IT mentions map to internal university application evidence
- Applied Benefits, Applied Systems, benefits, and insurance software mentions map to benefits and insurance workflow evidence
- C#, .NET, SQL, Angular, and TypeScript mentions map to technology evidence
- snippets that contain scaffold or guardrail text are filtered out before they can become evidence cards
- template instructions are blocked (for example: "Add verified...", "Project Template", "Context:", "Purpose:", "Role:")

Preferred source order:
- profile/cover_letter_evidence.md is preferred for applicant-facing proof points.
- Other allowlisted profile files are used only when snippets pass scaffold and applicant-facing filters.

Dirty evidence definition (blocked at retrieval, brief validation, polishing, and final validation) includes:
- TODO/TBD/FIXME placeholders
- template headings and instructions (Project Template, Candidate Project Leads, Manual Review Notes, Technical Skills To Verify)
- scaffold labels like Context:, Purpose:, Role:, Technologies:, What was built:, Outcome:
- claim-boundary/instruction phrases such as where supported by actual projects, safe themes to verify, verify and expand, and claims to avoid
- sentence-level placeholder/verification leakage patterns (for example: "Placeholder:" and "where verified by project history")

Applicant-facing evidence checks are stricter than basic dirty filtering:
- dirty filtering removes obvious scaffold/instructional content
- applicant-facing checks additionally reject sentence patterns that read like verification notes, TODOs, or disjunctive checklist language instead of resume-ready proof points

Draft and final model prompts require using 1 to 2 evidence cards naturally, rather than generic enterprise phrasing.

## Evidence Vs Claim Boundaries

Evidence cards are applicant-facing proof points suitable for prose.
Claim boundaries are safety constraints that limit overstatement.

- Evidence cards should read like concrete accomplishments.
- Claim boundaries should never be pasted into applicant-facing sentences.

Examples of good evidence snippets:
- "Contributed feature work and unit-tested business-rule changes in BizLink, an enterprise-grade insurance quoting workflow."
- "Improved user-facing workflow behavior by adjusting form and validation flow in internal applications."

Examples of bad snippets that are filtered:
- "C#, .NET, ASP.NET, or .NET Core work where supported by actual projects."
- "Safe themes to verify and expand before final output."
- "Claims to avoid unless verified."
- "Add verified academic, internship, professional, and personal projects here."
- "Context:" / "Purpose:" / "Role:" template labels

Use profile/cover_letter_evidence.md for curated, applicant-facing proof points. Keep entries conservative and factual. Do not include ownership claims, unverified metrics, or claim-boundary checklist text.

## Why Raw Missing Skills Are Excluded

Missing skills are useful for internal decision support, not for candidate-facing prose.
Passing raw missing skills into final generation encourages self-disqualifying language and weak letters.
The Letter Brief approach keeps internal risk signals available in analysis artifacts while preventing them from leaking into final cover letter output.
