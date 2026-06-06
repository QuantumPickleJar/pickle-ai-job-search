# Cloud-to-Local Migration Audit

## Scope And Method

This audit searched tracked repository files for:

```text
claude
anthropic
openai
api_key
apiKey
client.messages
chat.completions
completion
/commands
/skills
model
```

It also inspected Claude/Codex agent definitions and workflow files that use model reasoning, `Agent`, `WebFetch`, or `WebSearch` without naming a vendor directly.

## Summary

- 18 tracked files contain explicit Claude, Anthropic, OpenAI, or Gemini references.
- 0 executable Anthropic or OpenAI SDK/API call sites were found.
- 0 configured Anthropic or OpenAI API keys were found.
- 2 agent definitions directly instruct an external Gemini CLI invocation.
- The main cloud dependency is implicit: Markdown commands and skills are executed by Claude Code and rely on its model/tool runtime.
- The Python local-first path already routes fit scoring through `ModelProvider` and `OllamaProvider`.

The repository is therefore not migrating from a conventional Anthropic/OpenAI SDK integration. It is migrating selected capabilities out of a Claude Code-hosted workflow into explicit local Python workflows.

## Explicit Vendor Reference Inventory

The 18 files with explicit vendor references are:

```text
.claude/agents/gemini-research-expert.md
.claude/commands/apply.md
.claude/commands/expand.md
.claude/commands/reset.md
.claude/commands/setup.md
.claude/skills/upskill/SKILL.md
.codex/agents/gemini-research-expert.toml
.gitignore
CLAUDE.md
README.md
SETUP.md
docs/00-project-constraints.md
docs/01-windows-local-runtime.md
docs/02-model-provider-abstraction.md
docs/09-cloud-to-local-migration.md
docs/10-acceptance-tests.md
docs/phase-2-implementation-plan.md
documents/README.md
```

Two additional workflow files are implicitly model-dependent even though they do not name a vendor:

```text
.claude/skills/job-application-assistant/SKILL.md
.claude/skills/job-scraper/SKILL.md
```

## File Classification

| File or group | What it appears to do | Classification | Migration priority |
|---|---|---|---|
| `.claude/commands/apply.md` | Fetches/parses a job, evaluates fit, drafts CV and cover letter, spawns a reviewer agent, revises, compiles LaTeX, and inspects PDFs. | Command workflow; LaTeX generation; PDF repair loop | Priority 1 for extraction/evaluation, Priority 2 for targeting/drafting, Priority 3 for LaTeX/PDF |
| `.claude/skills/job-application-assistant/SKILL.md` | Defines the general evaluate, tailor CV, write cover letter, and interview-prep workflow. | Command workflow | Priorities 1-4 by step |
| `.claude/skills/job-scraper/SKILL.md` | Uses WebSearch/WebFetch, deduplicates results, and performs quick fit assessments. | Scraper/search logic; command workflow | Priority 1 |
| `.claude/skills/upskill/SKILL.md` | Fetches postings, performs hard-skill diff and LLM synthesis, searches for learning resources, and writes reports. | Command workflow; scraper/search logic | Priority 4 |
| `.claude/commands/setup.md` | Reads candidate documents, resolves conflicts, synthesizes profile material, and updates Claude-specific skill/profile files. | Command workflow | Priority 4; retain until local profile curation is mature |
| `.claude/commands/expand.md` | Enriches the candidate profile from documents, GitHub, public URLs, and web research. | Command workflow; scraper/search logic | Priority 4 |
| `.claude/commands/reset.md` | Clears or resets Claude-oriented profile/document state. It does not contain a provider API call. | Command workflow | Low priority |
| `.claude/agents/gemini-research-expert.md` | Defines a Claude sub-agent using a Sonnet model that shells out to `gemini -p`. | Model call / external model command | Priority 4; optional provider or research-service replacement |
| `.codex/agents/gemini-research-expert.toml` | Codex form of the same Gemini CLI research-agent instructions. | Model call / external model command | Priority 4 |
| `.claude/settings.local.json` | Grants Claude Code tool permissions for skills, Python, curl, and Bun. No provider or API key is configured. | Configuration | Do not migrate yet |
| `.claude/skills/job-application-assistant/01-07*.md` | Candidate facts, writing rules, evaluation framework, LaTeX guidance, and interview guidance consumed by Claude workflows. | Documentation/prompt assets; some LaTeX generation guidance | Keep as compatibility assets; migrate content selectively |
| `CLAUDE.md` | Claude-specific candidate profile and workflow rules. | Documentation/prompt configuration | Do not remove; preserve upstream compatibility |
| `README.md` | Describes Claude Code as the primary workflow and documents commands and prerequisites. | Documentation only | Update after local workflow reaches feature parity |
| `SETUP.md` | Installs/configures Claude Code and describes Claude onboarding. | Documentation only | Update later with dual Claude/local setup |
| `documents/README.md` | Explains how `/setup` reads documents into Claude skill files. | Documentation only | Update after local document ingestion exists |
| `.gitignore` | Contains a Claude-specific user memory ignore. | Configuration/documentation | No migration needed |
| `docs/00`, `01`, `02`, `09`, `10`, and `phase-2-implementation-plan.md` | Describe local-first constraints, Ollama, provider abstraction, migration, tests, and implementation order. | Documentation only | Source of truth; do not migrate |
| `ai_job_search/model_provider.py` | Defines `ModelRequest`, `ModelResponse`, `ModelProvider`, and provider error types. | Local provider abstraction | Already migrated foundation |
| `ai_job_search/providers/ollama.py` | Calls local Ollama `/api/chat`, supports JSON mode, and handles connection/model/timeout/shape errors. | Local model call | Already migrated foundation |
| `ai_job_search/fit_scoring.py` | Builds conservative fit prompts and calls a generic `ModelProvider`. | Local model workflow | Priority 1 already implemented |
| `ai_job_search/apply_from_file.py` | Creates a reviewable application workspace and requests fit scoring through an injected provider. | Local workflow | Priority 2 foundation already implemented |
| `scripts/model_provider_demo.py`, `score_fit.py`, `apply_from_file.py` | CLI entry points for the provider and local workflows. | Local workflow | Already provider-routed |
| `.agents/skills/*-search/cli/` | Bun/TypeScript job-board search tools. They call job-board endpoints, not cloud-model APIs. | Scraper/search logic | Integrate normalized output later; no model migration required |
| `cv/main_example.tex`, `cover_letters/cover.cls`, `cover_letters/OpenFonts/` | Static LaTeX templates and fonts. | LaTeX generation assets | Priority 3 workflow integration; files themselves need no provider migration |

## Direct API Findings

No executable uses of the following were found:

```text
anthropic.messages.create
client.messages
openai
chat.completions
ANTHROPIC_API_KEY
OPENAI_API_KEY
```

The occurrences of `client.messages`, `chat.completions`, Anthropic, and OpenAI in `docs/02-model-provider-abstraction.md` and `docs/09-cloud-to-local-migration.md` are design examples, not runtime calls.

`SETUP.md` mentions an Anthropic API key or Claude subscription as a Claude Code prerequisite, but no key is read or stored by repository code.

## Recommended Migration Priority

### Priority 1: Job Extraction, Fit Scoring, Keyword Matching

Highest-value targets:

1. `.claude/commands/apply.md`, Steps 0-1.
2. `.claude/skills/job-scraper/SKILL.md`, Steps 2-3.
3. `.claude/skills/job-application-assistant/SKILL.md`, Step 1.

Recommended approach:

- Normalize all captured/fetched jobs through `ai_job_search.intake` and `job_validation`.
- Keep portal retrieval separate from model reasoning.
- Route requirement extraction, fit scoring, and keyword matching through:

```python
response = provider.complete(
    ModelRequest(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_format="json",
    )
)
```

- Reuse `ai_job_search.fit_scoring` as the first migrated path.
- Preserve raw model responses when JSON parsing or schema validation fails.

### Priority 2: Resume Targeting Notes And Cover Letter Drafting

Targets:

1. `.claude/commands/apply.md`, Steps 2-4.
2. `.claude/skills/job-application-assistant/SKILL.md`, Steps 2-3.
3. `ai_job_search/apply_from_file.py`.

Recommended approach:

- Add separate provider-neutral prompts for resume targeting and cover-letter notes.
- Ground prompts only in normalized job JSON and curated `profile/` facts.
- Produce editable Markdown first.
- Require human review before any LaTeX files are written.
- Do not let provider output overwrite final documents directly.

### Priority 3: LaTeX Generation And PDF Repair Loop

Targets:

1. `.claude/commands/apply.md`, Steps 5-6.
2. `.claude/skills/job-application-assistant/05-cv-templates.md`.
3. `.claude/skills/job-application-assistant/06-cover-letter-templates.md`.

Recommended approach:

- Keep LaTeX compilation deterministic and outside the model provider.
- Use provider calls only for bounded content suggestions or repair recommendations.
- Validate generated `.tex`, compile with `lualatex`/`xelatex`, inspect page counts, and retain the mandatory human review.
- Preserve the current Claude workflow until local PDF inspection and repair have equivalent safeguards.

### Priority 4: Interview Prep, Tracking, Setup, Expansion, And Upskilling

Targets:

1. `.claude/skills/job-application-assistant/SKILL.md`, Step 4.
2. `.claude/skills/upskill/SKILL.md`.
3. `.claude/commands/setup.md`.
4. `.claude/commands/expand.md`.
5. Gemini research-agent definitions.

Recommended approach:

- Migrate only after the local capture-to-fit-to-application-notes path is stable.
- Separate web research from model synthesis.
- Consider a provider-neutral research interface before replacing Gemini agent definitions.
- Keep setup/profile curation human-reviewed and idempotent.

## Proposed Replacement Pattern

Workflow modules should depend on the protocol, not on Ollama:

```python
from ai_job_search.model_provider import ModelProvider, ModelRequest


def run_workflow(provider: ModelProvider, context: str) -> str:
    response = provider.complete(
        ModelRequest(
            system_prompt="Return grounded output only.",
            user_prompt=context,
            temperature=0,
            response_format="json",
        )
    )
    return response.text
```

Provider selection should occur only at a CLI/application boundary:

```python
from ai_job_search.providers import OllamaProvider

provider = OllamaProvider()
result = run_workflow(provider, context)
```

Recommended future factory:

```python
def create_model_provider(name: str | None = None) -> ModelProvider:
    provider_name = name or os.environ.get("MODEL_PROVIDER", "ollama")
    if provider_name == "ollama":
        return OllamaProvider()
    raise ValueError(f"Unsupported model provider: {provider_name}")
```

Do not add OpenAI or Anthropic adapters until there is a concrete need. Their future implementations should satisfy the same protocol and remain optional.

## Risks

- Claude command workflows combine orchestration, web research, reasoning, file editing, and verification in one prompt. Migrating them wholesale would create a large regression surface.
- Local models may return malformed JSON, miss requirements, or inflate fit scores.
- Company research requires current web access; `ModelProvider` alone is not a replacement for WebSearch/WebFetch.
- The Claude reviewer-agent step provides context separation that a single local call may not reproduce.
- Profile facts exist in both `.claude/skills/` and `profile/`, creating drift risk until one synchronization strategy is defined.
- Local LaTeX generation without the existing compile-and-inspect loop may produce broken PDFs.
- Gemini research definitions are duplicated between `.claude/agents/` and `.codex/agents/`.
- README/setup changes made too early could imply feature parity that the local workflow does not yet have.
- Adding cloud adapters could accidentally introduce paid-key requirements into the default path.

## Files That Should Not Be Modified Yet

- `.claude/commands/apply.md`
- `.claude/commands/setup.md`
- `.claude/commands/expand.md`
- `.claude/commands/reset.md`
- `.claude/skills/job-application-assistant/*`
- `.claude/skills/job-scraper/*`
- `.claude/skills/upskill/*`
- `CLAUDE.md`
- `.claude/settings.local.json`
- `.claude/agents/gemini-research-expert.md`
- `.codex/agents/gemini-research-expert.toml`
- `cv/main_example.tex`
- `cover_letters/cover.cls`
- `.agents/skills/*-search/cli/`

These files preserve upstream Claude compatibility, current search behavior, or the verified LaTeX workflow. Migrate them only through narrow, separately tested changes.

## Audit Conclusion

The minimum useful local path is already present:

```text
captured job JSON
  -> schema validation
  -> ModelProvider
  -> OllamaProvider
  -> fit-analysis.json
  -> editable application notes
```

The next migration should not be a broad rewrite of Claude files. It should extend the provider-neutral Python path with grounded resume-targeting and cover-letter-note prompts, while leaving the Claude `/apply` workflow available as the higher-assurance document-generation path.
