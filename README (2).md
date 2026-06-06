# Phase 3 Pack — Deployable Local-First Job Application Service

This pack is for the third phase of the local-first `ai-job-search` fork.

Phase 1 created planning documents. Phase 2 implemented or planned local job intake, schema validation, Ollama-backed model calls, local fit scoring, and apply-from-file workflows.

Phase 3 turns those pieces into a user-friendly deployable service that can be reached away from home while keeping the actual model workload on the Windows machine with the RTX 3060.

## Intended architecture

```text
Remote device / laptop / phone
        ↓
Secure access layer
Tailscale Serve, Tailscale Funnel, Cloudflare Tunnel, or private VPN
        ↓
Raspberry Pi service container
ai-job-service web/API
        ↓
Windows 11 model workstation
Ollama on RTX 3060 12GB
```

## Strong recommendation

Do not expose Ollama directly to the public internet.

Instead, expose the application service, authenticate it, and let that service talk to Ollama over a private LAN or Tailscale address.

## Why the Raspberry Pi should host the service

The Pi is good for lightweight web UI, REST API, job intake, job queue, application workspace storage, reverse proxy integration, and always-on service hosting.

The Windows RTX 3060 machine is good for Ollama, local model inference, heavier document generation, and application scoring.

## Why use a container

A container keeps the app standalone and prevents it from tangling with other Raspberry Pi services.

The service should mount project data as volumes so captured jobs, generated notes, and application workspaces survive container rebuilds.

## Suggested Phase 3 implementation order

1. Read `docs/phase-3-service-architecture.md`
2. Run `p3-prompts/p3-00-service-architecture-doc.md`
3. Run `p3-prompts/p3-01-service-readme.md`
4. Run `p3-prompts/p3-02-containerized-service-skeleton.md`
5. Run `p3-prompts/p3-03-service-config-and-healthchecks.md`
6. Run `p3-prompts/p3-04-service-api-contract.md`
7. Run `p3-prompts/p3-05-job-queue-and-async-processing.md`
8. Run `p3-prompts/p3-06-basic-web-ui.md`
9. Run `p3-prompts/p3-07-secure-remote-access-docs.md`
10. Run `p3-prompts/p3-08-deployment-compose-pi.md`
11. Run `p3-prompts/p3-09-readme-polish-and-runbook.md`
12. Run `p3-prompts/p3-10-phase-3-acceptance-tests.md`

Do not run every prompt at once. Feed them to Codex one at a time and review each diff.
