# Phase 3 Service Architecture

## Purpose

Phase 3 turns the Phase 2 local-first workflow into a lightweight, remotely reachable service without moving model inference or private application data to a paid cloud provider.

The target architecture is:

```text
Remote browser or LinkedIn clipper
                |
                | HTTPS or private overlay network
                v
Secure access layer
Tailscale, Cloudflare Tunnel + Access, or private VPN
                |
                | authenticated service traffic
                v
Raspberry Pi
containerized ai-job-service web/API
                |
                | private LAN or tailnet only
                v
Windows 11 workstation
Ollama on NVIDIA RTX 3060 12GB
```

The remote client communicates with the application service. It never communicates directly with Ollama.

## Architectural Goals

- Preserve the local-first, no-paid-model requirement of Phases 1 and 2.
- Keep the Raspberry Pi service lightweight, standalone, and containerized.
- Use the Windows workstation GPU for model inference.
- Make captured jobs and generated application workspaces survive container replacement.
- Permit secure remote use without publishing Ollama to the internet.
- Retain human review before any application material is used or submitted.
- Preserve the LinkedIn safety boundary: one user-triggered current-page capture only.

## Component Responsibilities

### Remote browser or extension

The client may be the service web UI, a local client, or the LinkedIn job clipper.

It is responsible for:

- collecting one job posting after an explicit user action;
- previewing captured fields before submission;
- sending the normalized capture to the authenticated Pi service; and
- displaying processing status and reviewable outputs.

In a remote deployment, the extension target is the protected Pi service URL, not `localhost` on the remote device. The target URL must be configurable in a later Phase 3 implementation.

The client must not:

- crawl LinkedIn search results;
- capture multiple postings in one action;
- automate infinite scrolling or next-job navigation;
- automate Easy Apply or application submission;
- scrape profiles, recruiters, contacts, messages, cookies, or login data.

### Secure access layer

The secure access layer is the only supported remote entry point. Acceptable options are:

1. **Tailscale private tailnet access**, recommended by default.
2. **Cloudflare Tunnel with a Cloudflare Access policy**, when browser-friendly public HTTPS is needed.
3. **A private VPN**, when an existing VPN already connects the remote client to the home network.

The access layer is responsible for:

- authenticating the remote user or device;
- encrypting traffic in transit;
- forwarding only the application service;
- avoiding direct public port forwarding; and
- limiting who can reach job data and generated documents.

Tailscale Funnel or an unauthenticated public reverse proxy is not part of the default design. If a public ingress option is evaluated later, it must add explicit identity policy, HTTPS, rate limiting, and service-level authorization.

### Port forwarding capability

The deployment can use an arbitrary external port when the network requires traditional router port forwarding. The application keeps `3927` as its container-internal default, while Docker Compose or a reverse proxy may map a different host or external port to it.

Port forwarding does not replace authentication or encryption:

- **Tailscale or Cloudflare Tunnel:** normally requires no router port forward.
- **Private VPN:** forward only the VPN listener port, then access the Pi service through the VPN.
- **Authenticated reverse proxy:** an external port such as `443` or `8443` may forward to a TLS-enabled reverse proxy on the Pi, which then forwards authenticated traffic to the application service.
- **Direct application port forwarding:** not part of the default architecture. It must wait until the service has strong authentication, HTTPS, rate limiting, restricted CORS, and a reviewed firewall policy.

Using an unusual high-numbered port is not a security control. Under no deployment option should router port forwarding target Ollama port `11434`.

### Raspberry Pi hosted service

The Pi runs an always-on, lightweight web/API service. It should be deployable independently with Docker and Docker Compose on a supported ARM64 Raspberry Pi OS host.

The service is responsible for:

- authenticated job intake;
- job schema validation and normalization;
- persisted job and application metadata;
- fit-scoring and apply-from-file orchestration;
- status reporting for work waiting on Ollama;
- serving reviewable Markdown and JSON outputs;
- health checks for the service and Ollama connectivity; and
- calling the generic `ModelProvider`, configured for Ollama.

The Pi is not expected to run the primary language model. It should remain useful for capture and browsing when the Windows workstation is asleep, with model-dependent work reported as pending or unavailable rather than lost.

The service must expose task-oriented application endpoints only. It must not become a generic public proxy to arbitrary Ollama endpoints or prompts.

### Windows 11 model workstation

The Windows 11 workstation runs:

- Ollama;
- the configured local model, initially `qwen2.5:14b`; and
- GPU inference on the RTX 3060 12GB.

The Pi service reaches Ollama through:

```text
OLLAMA_BASE_URL=http://<private-windows-address>:11434
```

The address may be a private LAN hostname/IP or a Tailscale address. `host.docker.internal` is appropriate only when the service container and Ollama run on the same machine; it is not the default for a Pi-to-Windows deployment.

Ollama may need to listen on an interface reachable from the Pi, but access must remain restricted to the private LAN or tailnet. Windows Firewall rules should limit port `11434` to the Pi address, trusted subnet, or tailnet as narrowly as practical.

Ollama must not be:

- exposed through router port forwarding;
- published through Cloudflare Tunnel;
- assigned a public unauthenticated URL; or
- made directly reachable by the remote browser or extension.

## Trust Boundaries

```text
Public or remote network
        |
        | Boundary 1: authenticated secure access
        v
Pi application service
        |
        | Boundary 2: private service-to-model network
        v
Windows Ollama endpoint
        |
        | Boundary 3: mounted private application data
        v
Pi host storage
```

Boundary requirements:

- **Boundary 1:** require tailnet/VPN membership or an Access identity policy. Do not rely on an obscure URL.
- **Boundary 2:** allow only the Pi service to reach Ollama where practical. Do not route the raw Ollama API through the public ingress.
- **Boundary 3:** mount only required data directories, use least-privilege filesystem permissions, and do not serve files without authorization checks.

A service API key may be added as defense in depth, especially for extension requests, but it does not replace the secure access layer. Secrets belong in an ignored environment file, container secret, or deployment-specific secret store.

## Request and Processing Flow

### Capture flow

1. The user opens one job posting manually.
2. The extension extracts and previews visible fields after the user clicks it.
3. The extension sends one capture request through the secure access layer.
4. The Pi service authenticates the request, enforces size and content-type limits, validates the JSON, and normalizes it.
5. The service writes the job into persistent intake storage.
6. The service returns a job identifier and saved status.

Capture must not require Ollama. A sleeping or unavailable Windows workstation must not prevent a valid job from being saved.

### Model-processing flow

1. The user explicitly requests fit scoring or application workspace preparation.
2. The Pi service persists a `queued` task and immediately returns its task identifier.
3. A single background worker marks the task `running` and loads the captured job and curated profile facts from mounted storage.
4. Workflow code calls the generic `ModelProvider`.
5. The Ollama provider sends the request to `${OLLAMA_BASE_URL}/api/chat` over the private network.
6. The service validates structured model output before accepting it.
7. Valid JSON and Markdown outputs are written to mounted application storage and the task is marked `succeeded`.
8. Malformed model output is retained for local diagnosis and the task is marked `failed`.
9. The user polls the task endpoint and reviews generated output before any final document or application action.

Task records are JSON files under mounted storage. Queued tasks are restored after a service restart. A task interrupted while `running` is marked `failed` because the single-process queue cannot prove that its work completed safely.

## Container and Compose Design

Phase 3 should add a dedicated service image and Compose deployment. The conceptual deployment is:

```text
Raspberry Pi host
  Docker Compose project
    ai-job-service container
      web/API process
      environment-based configuration
      mounted /app/data
    optional access connector container
      cloudflared, only for the Cloudflare option
```

The application container should:

- support ARM64;
- run as a non-root user where practical;
- receive configuration through environment variables;
- include a service health check;
- use restart policies suitable for an always-on Pi;
- avoid embedding profile data or secrets in the image; and
- remain independently deployable from unrelated Pi services.

The Tailscale option may run Tailscale on the Pi host or in a separately managed container. The architecture does not require the application container to control the host network.

## Persistent Data

Generated data must be mounted from Pi host storage or named Docker volumes. A recommended container layout is:

```text
/app/data/
  job_intake/
    captured_jobs/
    processed_jobs/
    rejected_jobs/
  applications/
  tasks/
  profile/
  logs/
```

The Compose deployment should map this to a host-owned directory such as:

```text
./data:/app/data
```

Persistence requirements:

- container rebuilds and restarts must not delete job or application data;
- profile facts and disallowed claims must remain editable outside the image;
- backups should cover the mounted data directory;
- generated and captured data should remain ignored by Git; and
- logs should avoid secrets, authentication headers, and unnecessary full job/profile content.

## Configuration Contract

The later service implementation should define at least:

```env
APP_HOST=127.0.0.1
APP_PORT=3927
APP_DATA_DIR=/app/data
OLLAMA_BASE_URL=http://<private-windows-address>:11434
OLLAMA_MODEL=qwen2.5:14b
APP_API_KEY=<deployment-secret>
ENABLE_REMOTE_MODE=false
```

Notes:

- `APP_HOST=127.0.0.1` is the safe default for Compose host publication through Tailscale Serve or Cloudflare Tunnel. Compose overrides the application process to listen on `0.0.0.0` inside the isolated container.
- `APP_PORT=3927` is the service's internal/default port. A later Compose file may map any available Pi host port to it without changing application behavior.
- `OLLAMA_BASE_URL` is resolved by the Pi service and must never point to a public Ollama endpoint.
- `APP_API_KEY` is a placeholder contract for future implementation and must not be committed with a real value.
- `ENABLE_REMOTE_MODE` should default to false until remote authentication and allowed origins are configured.
- A future `.env.example` may contain names and safe placeholders only.

## Network Exposure Matrix

| Component | Reachable by | Exposure rule |
|---|---|---|
| Pi web/API service | Authorized remote client and trusted LAN clients | Through Tailscale/VPN or Cloudflare Tunnel with Access |
| Pi persistent data | Pi service and local administrator | Never served as an unauthenticated directory |
| Windows Ollama API | Pi service over private LAN/tailnet | Never exposed to public internet |
| Docker/host administration | Local administrator | Not exposed by the application service |

For a LAN-only deployment, publish the Pi service only on the trusted LAN interface where practical. For Tailscale, prefer listening or firewalling so only tailnet traffic reaches the service. For Cloudflare, avoid an additional public host-port path that bypasses Access.

If router forwarding is used, it should terminate at the selected secure access component. The external port is deployment-specific; the internal service contract remains port `3927`.

## Failure and Availability Behavior

The service should distinguish these states:

- **Pi service unavailable:** the client explains that the protected service cannot be reached.
- **Authentication denied:** return an authorization error without revealing job data.
- **Invalid job JSON:** return field-level validation errors and write no misleading output.
- **Windows workstation offline:** preserve captured jobs and mark model work unavailable or pending.
- **Ollama reachable but model missing:** identify the configured model and the required local pull action.
- **Ollama timeout:** retain the job and expose a retryable failure state.
- **Malformed model output:** save raw output for diagnosis, do not write a valid-looking analysis.
- **Mounted storage unavailable:** fail writes clearly and report unhealthy status.

## Security and Privacy Requirements

- Do not commit `.env` files, API keys, tunnel credentials, VPN keys, or Access tokens.
- Use exact CORS origins rather than `*` when browser access is enabled.
- Authenticate before returning job descriptions, profile facts, or generated application files.
- Apply request body limits and sensible model timeouts.
- Do not interpolate user input into filesystem paths without safe identifiers.
- Preserve the Phase 2 claim-safety rules and load `profile/disallowed_claims.md` during model work.
- Treat local model output as advisory and require human review.
- Keep capture and submission separate; this service does not submit job applications.

## Non-Goals

Phase 3 does not include:

- public SaaS or multi-user tenancy;
- hosting the primary model on the Raspberry Pi;
- exposing Ollama directly;
- paid model providers;
- LinkedIn crawling, search automation, or bulk capture;
- Easy Apply, form filling, or application submission;
- recruiter, profile, contact, or message scraping;
- anti-bot bypassing;
- final resume or cover-letter quality without human review.

## Deployment and Verification Commands

The service image can be built from the repository root:

```bash
docker build -f service/Dockerfile -t ai-job-service .
docker run --rm \
  --env-file service/.env \
  -v "$(pwd)/data:/app/data" \
  -p 3927:3927 \
  ai-job-service
```

The later Raspberry Pi Compose prompt is expected to support:

```bash
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f ai-job-service
docker compose down
```

Implemented health checks:

```bash
curl http://localhost:3927/health
curl http://localhost:3927/health/ollama
```

## Assumptions

- The deployment is single-user or single-household.
- The Raspberry Pi runs a supported 64-bit OS with Docker Compose.
- The Pi has persistent local storage with adequate backup and filesystem permissions.
- The Pi and Windows workstation share a private LAN, tailnet, or equivalent private route.
- The Windows workstation may sleep or be turned off; capture remains available without inference.
- Ollama remains the default provider and the existing `ModelProvider` abstraction is reused.
- Remote access policy is configured outside the application before remote mode is enabled.

## Acceptance Criteria for the Architecture

The architecture is ready for implementation when:

- remote clients reach only an authenticated Pi application service;
- the Pi service is independently deployable through Docker Compose;
- the Pi calls Ollama only through configured `OLLAMA_BASE_URL`;
- Ollama is reachable only over a private network path;
- mounted data survives container replacement;
- capture works independently of model availability;
- model output remains validated and reviewable;
- no crawling, bulk capture, Easy Apply, or submission automation is introduced; and
- deployment secrets remain outside version control.

## Next Phase 3 Work

The next prompt should create the user-facing service README and deployment prerequisites based on this architecture. Subsequent prompts can add:

1. the containerized service skeleton and Dockerfile;
2. configuration loading and health checks;
3. authenticated API contracts;
4. a basic review-oriented web UI;
5. secure remote access runbooks;
6. Raspberry Pi Compose deployment; and
7. Phase 3 acceptance tests.
