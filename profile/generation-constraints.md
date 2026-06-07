# AI Generation Constraints

This file documents constraints for AI-generated application materials (cover letters, CV suggestions, fit analyses).

## Enterprise Experience Rules

**CRITICAL CONSTRAINT**: The candidate HAS professional enterprise application experience.

### DO

- ✓ "Contributed to an enterprise-grade insurance quoting system"
- ✓ "Worked on backend features for internal university enterprise applications"
- ✓ "Implemented business rules and tests in an enterprise software system"
- ✓ "Collaborated on enterprise application development in a professional environment"
- ✓ If a role requires senior enterprise architecture ownership: "Candidate has enterprise application experience but may lack senior architecture ownership."

### DO NOT

- ✗ "The candidate lacks enterprise application experience"
- ✗ "No business application background"
- ✗ "Limited exposure to enterprise-grade systems"
- ✗ "Never worked on enterprise software"
- ✗ "Candidate has only academic or junior project experience"

## Ownership & Architecture Claims

**STRICTLY PROHIBITED**: Do not claim the candidate owns or architected these systems:
- BizLink (Secura insurance quoting platform)
- AgencyPortal (insurance agency management)
- PowerWriter (form/document generation)
- ImageRight (document management)
- UWO Portal (university application/housing)
- Applied Benefits / Dynamic Plan Benefits Designer (benefits administration platform)

**PERMITTED ATTRIBUTION**: Contributions to these systems should use:
- "Contributed to", "Worked on", "Implemented features in", "Modified rules in", "Tested and debugged"
- "Collaborated with team members on", "Supported the development of", "Added tests for"

## Context Loading

The following profile files MUST be loaded when generating application materials:
- `profile/resume_facts.md` - Contains verified enterprise experience documentation
- `profile/disallowed_claims.md` - Contains explicit constraints (including ownership rules)
- `profile/project_inventory.md` - Contains project context and safe/unsafe claims
- `profile/experience_bullets.md` - Contains pre-vetted bullet points
- `profile/skills_inventory.md` - Contains skills and technology claims

These files are referenced in `ai_job_search/fit_scoring.py` in the `PROFILE_CONTEXT_FILES` constant.

## Regression Testing

A regression test fixture is provided: `job_intake/examples/regression-enterprise-experience-job.json`

This posting specifically requests "professional experience contributing to enterprise-grade business applications".

The fit analysis for this posting MUST NOT claim:
- That the candidate lacks enterprise experience
- That the candidate has no business application background
- That the candidate is unsuitable for this reason

**Acceptable outcomes**:
1. High score/apply recommendation because of documented enterprise experience
2. Moderate score with specific gaps framed as seniority/ownership gaps, not experience gaps

Run the test with:
```
python scripts/score_fit.py job_intake/examples/regression-enterprise-experience-job.json
```

Then verify `applications/examples/regression-enterprise-experience-job/fit-analysis.json` does not contain enterprise experience claims in the `missing_skills` or `risks` fields.

## Generated Material Verification

Before generating cover letters or CV suggestions:
1. Verify that `profile/resume_facts.md` was loaded (check application context)
2. Verify that enterprise project descriptions are included
3. If generating for an enterprise application role, ensure framing acknowledges experience
4. Check all ownership claims against `disallowed_claims.md`
5. Do not invent technologies, companies, or project details not in the profile

