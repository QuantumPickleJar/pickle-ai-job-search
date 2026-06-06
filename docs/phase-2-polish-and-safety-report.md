# Phase 2 Polish and Safety Report

## Summary

Phase 2 is ready for manual local testing after Ollama is started and the profile scaffold is reviewed. The implementation remains local-first, user-triggered, and review-oriented. It does not add crawling, bulk capture, automated application submission, or paid model providers.

## What Was Checked

### Documentation and Windows usability

- Reviewed the root `README.md` and Phase 2 planning documents.
- Verified PowerShell-friendly commands for Ollama setup, job validation, fit scoring, apply-from-file, the intake server, and acceptance tests.
- Verified the extension README includes unpacked installation steps for Chrome and Microsoft Edge.
- Verified the documentation distinguishes the local Phase 2 workflow from the upstream Claude workflow.

### LinkedIn safety

- Confirmed capture begins only after the user opens the extension popup.
- Confirmed the extension handles only the current LinkedIn job page.
- Confirmed there is no search crawling, infinite scrolling, bulk capture, next-job automation, Easy Apply automation, messaging, or login/cookie extraction.
- Confirmed the extension does not scrape profiles, recruiters, or contacts.
- Confirmed captured data is posted only to `http://localhost:3927/jobs/capture`.
- Confirmed the intake server refuses non-localhost bind addresses.
- Confirmed CORS does not use a wildcard origin.

### Secrets and privacy

- Searched the repository for common API-key, secret, token, password, and authorization patterns.
- Found no committed API key or paid-provider credential.
- Confirmed the committed sample job uses a placeholder company and explicitly states that it is not a real posting.
- Confirmed no captured real job JSON is present in `job_intake/captured_jobs/`.
- Confirmed profile scaffolding contains no private address or phone number.
- Reviewed tracked files under `job_intake/`, `applications/`, and `profile/`.

### Local model behavior

- Confirmed fit scoring requests JSON-only output.
- Confirmed generated fit analysis is parsed and shape-validated before it is saved.
- Confirmed malformed or invalid model output is preserved as `fit-analysis.raw.txt` with a clear error.
- Confirmed the scoring prompt requires conservative ratings, honest missing skills, and no unsupported claims.
- Confirmed the Ollama provider reports unreachable-server, timeout, missing-model, and invalid-response errors.

### Workflow error handling

- Confirmed invalid job JSON produces field-level validation errors.
- Confirmed the intake server returns structured JSON errors for invalid requests.
- Confirmed the extension disables saving when required captured fields are missing.
- Confirmed acceptance tests distinguish pass, fail, and skipped model-dependent checks.

## Problems Found

1. The root README did not explain the Phase 2 local-first workflow or its Windows commands.
2. `.env` files and local captured/generated workflow data were not explicitly ignored.
3. Fit scoring loaded `profile/resume_facts.md` but did not load `profile/disallowed_claims.md`.
4. When the intake server was offline, the extension could show only a generic browser fetch error.
5. Full Ollama-backed scoring and apply-from-file verification could not run while the local Ollama service was unavailable.

## Problems Fixed

### Root workflow documentation

Added a Phase 2 local-first section to `README.md` covering:

- Windows 11 and Ollama setup.
- PowerShell model selection with `OLLAMA_MODEL`.
- Job validation and local fit scoring.
- Apply-from-file workspace generation.
- Local intake server startup.
- Chrome/Edge extension installation reference.
- Acceptance tests.
- Explicit automation exclusions and privacy guidance.

### Ignore rules

Updated `.gitignore` to exclude:

- `.env` and `.env.*` files, while permitting a future `.env.example`.
- Captured, processed, and rejected local job JSON files.
- Generated application JSON, Markdown, raw model output, and generated document folders.

### Prompt grounding

Updated fit-scoring profile context loading so both of these files are supplied to the model when available:

- `profile/resume_facts.md`
- `profile/disallowed_claims.md`

The existing conservative scoring and JSON validation behavior remains unchanged.

### Extension error message

Updated the extension popup to tell the user when the localhost intake server is unreachable and show the PowerShell command needed to start it.

## Intentionally Left for Later

- No final resume or cover-letter generation was added.
- No LaTeX or PDF workflow was migrated to the local provider.
- No cloud provider implementation or paid API requirement was added.
- No automatic application submission, Easy Apply automation, crawling, or batch capture was added.
- The extension has no authentication token. This is acceptable for the current localhost-only MVP, but any future network exposure must add authentication and stricter origin controls.
- LinkedIn selectors are inherently fragile and need manual verification against current LinkedIn pages.
- The profile scaffold still needs exact titles, dates, degree names, project details, and verified achievements before real application use.
- Profile files are tracked scaffolds. Users must review staged changes carefully before committing personal additions.
- Full model-dependent acceptance checks remain pending until Ollama is running with the configured model installed.

## Remaining Risks

- A local browser extension other than this clipper could potentially call the localhost endpoint because extension origins are allowed by prefix rather than an installation-specific ID.
- Local model output may still be factually weak even when it is valid JSON. Human review remains mandatory.
- A selected model may not follow structured-output instructions consistently; malformed output is retained for diagnosis rather than silently accepted.
- LinkedIn DOM changes may reduce extraction quality or cause required fields to be missing.
- Generated job and application files can contain sensitive employment-search information even though Git now ignores them. Local filesystem access and backups should be handled accordingly.

## Recommended Next Phase

1. Start Ollama and pull the configured model.
2. Run `python scripts\run_acceptance_tests.py` and require all five checks to pass.
3. Manually test one placeholder LinkedIn capture in Chrome or Edge.
4. Review every file under `profile/` and replace placeholders only with verified facts.
5. Add focused automated tests with a fake `ModelProvider` so malformed, missing-field, and application-workspace behavior can be tested without running a model.
6. Begin the document-generation phase only after the local capture, scoring, and application-workspace path passes manual review.

