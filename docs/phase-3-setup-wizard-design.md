# Phase 3 Setup Wizard Design

## Purpose

The setup wizard configures this repository according to the role of the current machine while preserving the split runtime:

- the Raspberry Pi or Linux service host runs the containerized web/API service;
- the Windows model runner runs Ollama and GPU inference; and
- an all-in-one development machine may run both locally.

The wizard uses only the Python standard library so it can run from a fresh clone.

## Entrypoint

```bash
python scripts/setup_wizard.py
```

Supported roles:

```text
model-runner
service-host
all-in-one
diagnostics
```

The `--diagnostics` flag is equivalent to the diagnostics role and never changes files. `--dry-run` previews role changes without writing files or creating directories.

## Configuration Contract

`.env` is local and ignored by Git. The wizard creates it from `.env.example` only when needed and updates known keys without deleting unknown values or comments.

Important values:

```env
PUID=1000
PGID=1000
APP_HOST=127.0.0.1
APP_PORT=3927
APP_DATA_DIR=/app/data
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:14b
APP_API_KEY=<local-secret>
ENABLE_REMOTE_MODE=false
```

`APP_HOST` is the Pi host interface used by the Compose port publication. Compose overrides the application process to listen on `0.0.0.0` inside the isolated container.

The safe default is `127.0.0.1`. A LAN address or `0.0.0.0` requires an explicit warning and confirmation.

The service container runs as `PUID:PGID` so the mounted `./data` directory remains writable without using root.

## Role Behavior

### Model runner

- Detect the operating system.
- Locate the Ollama executable.
- Query local `/api/tags`.
- List installed models and check the configured model.
- Recommend `qwen2.5:14b` when no model is configured.
- Report a likely private `OLLAMA_BASE_URL` for the service host.
- Never change firewall, router, tunnel, or Ollama exposure settings.

### Service host

- Detect Linux and Raspberry Pi indicators.
- Check Docker and Docker Compose.
- Create or preserve `.env`.
- Configure the private Ollama URL and selected model.
- Create persistent data directories.
- Detect and write `PUID` and `PGID`.
- Default `APP_HOST` to `127.0.0.1`.
- Test Ollama connectivity without making it a prerequisite for capture.

### All in one

- Configure local Ollama at `http://localhost:11434`.
- Configure `APP_HOST=127.0.0.1`.
- Create persistent data directories.
- Check local Ollama.
- Print the Compose command used to start the service.

### Diagnostics

Diagnostics report operating system, repository detection, environment values, Docker, Compose, Ollama, model presence, data-directory state, writability, and service health. Diagnostics do not create a report or modify any other file.

## Setup Report

Mutating role runs create `docs/setup-wizard-report.md` with:

- UTC timestamp;
- selected role;
- masked values written;
- passed and failed checks;
- warnings; and
- next commands.

The full API key is never included.

## Safety Boundaries

The wizard must not:

- expose Ollama publicly;
- open firewall or router ports;
- install tunnel credentials;
- commit `.env`;
- invent private IP addresses;
- require paid APIs;
- crawl LinkedIn;
- automate Easy Apply; or
- submit applications.
