# Phase 3 Prompt Index

Run these prompts after Phase 2 is working or mostly working.

## Recommended order

| Order | Prompt | Purpose |
|---:|---|---|
| 0 | `p3-prompts/p3-00-service-architecture-doc.md` | Add/confirm Phase 3 architecture docs |
| 1 | `p3-prompts/p3-01-service-readme.md` | Add user-facing deployment README |
| 2 | `p3-prompts/p3-02-containerized-service-skeleton.md` | Create service app skeleton and Dockerfile |
| 3 | `p3-prompts/p3-03-service-config-and-healthchecks.md` | Add config and health checks |
| 4 | `p3-prompts/p3-04-service-api-contract.md` | Implement service API endpoints |
| 5 | `p3-prompts/p3-05-job-queue-and-async-processing.md` | Add queue/status handling for long model calls |
| 6 | `p3-prompts/p3-06-basic-web-ui.md` | Add basic browser UI |
| 7 | `p3-prompts/p3-07-secure-remote-access-docs.md` | Document Tailscale/Cloudflare options |
| 8 | `p3-prompts/p3-08-deployment-compose-pi.md` | Add Raspberry Pi Docker Compose deployment |
| 9 | `p3-prompts/p3-09-readme-polish-and-runbook.md` | Polish README and runbook |
| 10 | `p3-prompts/p3-10-phase-3-acceptance-tests.md` | Add service acceptance tests |

## Commit strategy

Commit after each successful prompt.

Suggested branch:

```bash
git checkout -b phase-3-deployable-service
```

Suggested commit messages:

```text
docs: add phase 3 service architecture
docs: add local-first service deployment readme
feat: add containerized job service skeleton
feat: add service config and health checks
feat: add service API contract
feat: add queued job processing
feat: add basic job service web UI
docs: add secure remote access guidance
deploy: add raspberry pi compose deployment
docs: polish service runbook
test: add phase 3 service acceptance tests
```
