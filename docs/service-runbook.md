# AI Job Service Runbook

## Purpose

This runbook covers routine operation and recovery of the Raspberry Pi hosted AI Job Search Service. It assumes:

- the repository is cloned on the Pi;
- `.env` exists and is not tracked by Git;
- `docker-compose.service.yml` is used;
- persistent data is bind-mounted from `./data`;
- the Pi reaches Ollama on a Windows workstation through a private LAN or tailnet; and
- commands are run from the repository root unless stated otherwise.

For first installation, use [`../deploy/raspberry-pi.md`](../deploy/raspberry-pi.md). For exposure choices, use [`remote-access.md`](remote-access.md).

## Preflight

Run diagnostics without changing files:

```bash
python3 scripts/setup_wizard.py --diagnostics
```

Validate Compose configuration:

```bash
docker compose -f docker-compose.service.yml --env-file .env config --quiet
```

Do not publish the full rendered Compose output in an issue because it may contain environment values.

## Wizard Operations

### When to Run the Wizard

Run the wizard on a machine when:

- performing a fresh clone;
- setting up a new role (model runner, service host, or all-in-one);
- a `.env` file does not yet exist;
- `PUID`, `PGID`, or `OLLAMA_BASE_URL` needs to be reconfigured; or
- verifying the deployment state before a support request.

Run diagnostics at any time to inspect current state without modifying files:

```bash
python3 scripts/setup_wizard.py --diagnostics
```

Diagnostics are safe to run on a live service.

### Re-running the Wizard Safely

The wizard updates known keys in `.env` without deleting unknown values or comments. It is safe to re-run:

```bash
python3 scripts/setup_wizard.py --role service-host
```

Preview changes without writing files:

```bash
python3 scripts/setup_wizard.py --role service-host --dry-run
```

After re-running a mutating role, compare `.env` with its previous state before restarting the service:

```bash
git diff .env  # will show nothing because .env is gitignored; use a manual diff instead
diff -u .env.example .env
```

Never replace `.env` wholesale. A full overwrite discards `APP_API_KEY` and machine-specific network settings.

## Start, Stop, and Restart

Start or reconcile the service:

```bash
docker compose -f docker-compose.service.yml --env-file .env up -d
```

Check status:

```bash
docker compose -f docker-compose.service.yml --env-file .env ps
curl --fail http://127.0.0.1:3927/health
```

Restart only the application container:

```bash
docker compose -f docker-compose.service.yml --env-file .env restart ai-job-service
```

Stop the service while retaining `./data`:

```bash
docker compose -f docker-compose.service.yml --env-file .env down
```

Do not add `--volumes` to normal shutdown commands.

## Logs

Follow logs:

```bash
docker compose -f docker-compose.service.yml --env-file .env logs -f ai-job-service
```

Show the most recent 200 lines:

```bash
docker compose -f docker-compose.service.yml --env-file .env logs --tail 200 ai-job-service
```

Show logs since a relative time:

```bash
docker compose -f docker-compose.service.yml --env-file .env logs --since 30m ai-job-service
```

Logs may contain job titles, validation errors, or file paths. Treat them as private application data.

## Update From Git

Before updating:

```bash
git status --short
git branch --show-current
```

Do not overwrite local changes. Commit, stash, or review them before continuing.

Fetch and fast-forward the current branch:

```bash
git fetch --prune
git pull --ff-only
```

Review deployment changes before rebuilding:

```bash
git diff HEAD@{1} -- .env.example docker-compose.service.yml service/ deploy/ docs/
```

The ignored `.env` file is not updated automatically when `.env.example` changes. Compare them and add new settings deliberately:

```bash
diff -u .env.example .env
```

Never replace `.env` wholesale during an update because that can discard the API key and machine-specific network settings.

## Rebuild the Container

Build without stopping the current container:

```bash
docker compose -f docker-compose.service.yml --env-file .env build ai-job-service
```

Recreate with the newly built image:

```bash
docker compose -f docker-compose.service.yml --env-file .env up -d --force-recreate ai-job-service
```

Verify:

```bash
docker compose -f docker-compose.service.yml --env-file .env ps
curl --fail http://127.0.0.1:3927/health
curl --fail http://127.0.0.1:3927/health/ollama
```

If verification fails, inspect logs before removing images or changing data.

## Check Ollama Connectivity

On the Windows model workstation, use PowerShell:

```powershell
ollama list
curl.exe http://localhost:11434/api/tags
python scripts/setup_wizard.py --role model-runner --dry-run
```

On the Pi, test the private URL configured in `.env`:

```bash
grep '^OLLAMA_BASE_URL=' .env
curl --fail http://<PRIVATE_WINDOWS_ADDRESS>:11434/api/tags
curl --fail http://127.0.0.1:3927/health/ollama
```

The first Pi request checks the private network path. The second checks the service's configured Ollama endpoint and model.

If connectivity fails, confirm:

- the Windows workstation is awake;
- Ollama is running;
- `OLLAMA_MODEL` appears in `ollama list`;
- the Pi can route to the configured private address; and
- Windows Firewall permits the Pi, trusted subnet, or tailnet.

Never resolve connectivity by forwarding port `11434`, creating a public tunnel to Ollama, or calling Ollama from browser JavaScript.

## Back Up Data

The `data/` directory may contain captured jobs, candidate profile facts, task state, and generated application material. Store backups securely.

For a consistent filesystem snapshot, stop the service briefly:

```bash
mkdir -p backups
chmod 700 backups
docker compose -f docker-compose.service.yml --env-file .env stop ai-job-service
tar -C . -czf "backups/ai-job-data-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" data
docker compose -f docker-compose.service.yml --env-file .env start ai-job-service
```

List and inspect an archive without extracting it:

```bash
ls -lh backups/
tar -tzf backups/<BACKUP_FILE>.tar.gz | head
```

Copy backups to encrypted storage or another trusted machine. A backup left only on the Pi does not protect against storage failure.

## Restore Data

Stop the service and preserve the current directory before restoring:

```bash
docker compose -f docker-compose.service.yml --env-file .env down
mv data "data.before-restore-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir data
tar -C . -xzf backups/<BACKUP_FILE>.tar.gz
```

Confirm that the archive restored a top-level `data/` directory:

```bash
find data -maxdepth 2 -type d -print
```

Restore ownership to the account identified by `PUID` and `PGID` in `.env`:

```bash
PUID_VALUE="$(sed -n 's/^PUID=//p' .env)"
PGID_VALUE="$(sed -n 's/^PGID=//p' .env)"
sudo chown -R "${PUID_VALUE}:${PGID_VALUE}" data
chmod 700 data
```

Start and verify:

```bash
docker compose -f docker-compose.service.yml --env-file .env up -d
curl --fail http://127.0.0.1:3927/health
```

Keep `data.before-restore-*` until the restored jobs, tasks, and applications have been reviewed.

## Rotate the API Key

Generate a replacement without embedding it in copied command history:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Open `.env`, replace only `APP_API_KEY`, and save it with restrictive permissions:

```bash
chmod 600 .env
```

Recreate the service so it reads the new environment:

```bash
docker compose -f docker-compose.service.yml --env-file .env up -d --force-recreate ai-job-service
```

Verify a mutating endpoint with the new key from an authorized client. Then update the manually configured key used by that client or extension. Do not place the key in Git, browser storage, screenshots, logs, or documentation.

Rotating `APP_API_KEY` does not replace Tailscale policy or Cloudflare Access. The application key protects mutating actions; the secure access layer must protect the entire service when it is remotely reachable.

## Change OLLAMA_BASE_URL

Use this procedure when the Windows workstation changes IP address, you switch from LAN to Tailscale (or vice versa), or you replace the workstation.

Back up data first (see **Back Up Data**).

Open `.env` and update `OLLAMA_BASE_URL`:

```bash
# Private LAN example
OLLAMA_BASE_URL=http://192.168.1.50:11434

# Tailscale example
OLLAMA_BASE_URL=http://100.x.y.z:11434
```

Do not use a public hostname, public IP, or Cloudflare Tunnel URL for `OLLAMA_BASE_URL`. Ollama must remain on the private network.

Recreate the service so it reads the new value:

```bash
docker compose -f docker-compose.service.yml --env-file .env up -d --force-recreate ai-job-service
```

Verify connectivity:

```bash
curl --fail http://127.0.0.1:3927/health/ollama
```

## Switch from Local-Only to LAN Mode

The default `APP_HOST=127.0.0.1` binds the published port to loopback. To allow LAN access change `APP_HOST` to the Pi's private LAN address or `0.0.0.0`.

> **Warning:** `APP_HOST=0.0.0.0` exposes the service on all interfaces. Do not do this without authentication (`APP_API_KEY`) and a reviewed secure access layer. An unusual port alone is not security.

Before changing:

1. Back up data (see **Back Up Data**).
2. Confirm `APP_API_KEY` is set to a strong secret.
3. Confirm `ENABLE_REMOTE_MODE` requirements are understood.

Edit `.env`:

```env
APP_HOST=0.0.0.0
```

Recreate the service:

```bash
docker compose -f docker-compose.service.yml --env-file .env up -d --force-recreate ai-job-service
```

Verify from a LAN device:

```bash
curl --fail http://<PI_LAN_ADDRESS>:3927/health
```

To revert to local-only, set `APP_HOST=127.0.0.1` and recreate.

## Use Tailscale Serve or Cloudflare Tunnel Safely

When publishing the service through Tailscale Serve or a Cloudflare Tunnel, keep `APP_HOST=127.0.0.1`. The tunnel proxy reaches the service over loopback; the port is never exposed to the broader network.

```env
APP_HOST=127.0.0.1
APP_PORT=3927
```

For Tailscale Serve:

```bash
tailscale serve https / http://127.0.0.1:3927
tailscale serve status
```

For Cloudflare Tunnel, configure the tunnel ingress to forward to `http://127.0.0.1:3927`. Require a Cloudflare Access identity policy on the public hostname. Do not create a bypass route around Access.

In both cases:

- Do not expose Ollama port `11434` through any tunnel or Serve rule.
- Keep `APP_API_KEY` configured as defense in depth.
- Do not commit tunnel credentials or Access tokens to Git.

## Check UID and GID

The service container runs as `PUID:PGID` so the mounted `./data` directory stays writable without using root.

Check the current account on the Pi:

```bash
id -u
id -g
```

Check the values recorded in `.env`:

```bash
grep -E '^(PUID|PGID)=' .env
```

Check ownership of the data directory:

```bash
ls -ld data
```

`PUID` and `PGID` must match the owner of `data/`. If they differ, fix ownership and recreate (see **Fix Root-Owned Data**).

## Fix Root-Owned Data

If `./data` is owned by root (common after running `docker compose up` as root or without `PUID`/`PGID` set), the service container cannot write to it.

Stop the service:

```bash
docker compose -f docker-compose.service.yml --env-file .env stop ai-job-service
```

Identify the target account:

```bash
id -u    # your Pi account UID
id -g    # your Pi account GID
```

Transfer ownership:

```bash
sudo chown -R "$(id -u):$(id -g)" data
chmod 700 data
```

Confirm `.env` has matching values:

```bash
grep -E '^(PUID|PGID)=' .env
```

Update if needed, then recreate:

```bash
docker compose -f docker-compose.service.yml --env-file .env up -d --force-recreate ai-job-service
curl --fail http://127.0.0.1:3927/health
```

Do not run the service as root or make `data` world-writable.

## Back Up Data Before Changing Deployment

Before any significant configuration change — rotating the API key, updating `OLLAMA_BASE_URL`, switching `APP_HOST`, upgrading the image, or restoring from backup — create a consistent snapshot first.

```bash
mkdir -p backups
chmod 700 backups
docker compose -f docker-compose.service.yml --env-file .env stop ai-job-service
tar -C . -czf "backups/ai-job-data-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" data
docker compose -f docker-compose.service.yml --env-file .env start ai-job-service
```

Verify the archive:

```bash
tar -tzf backups/<BACKUP_FILE>.tar.gz | head
```

Copy backups to encrypted storage or another trusted machine. A backup stored only on the Pi does not protect against storage failure. Keep the previous backup until the deployment change is verified.

### Service is unhealthy

```bash
docker compose -f docker-compose.service.yml --env-file .env ps
docker compose -f docker-compose.service.yml --env-file .env logs --tail 200 ai-job-service
curl -v http://127.0.0.1:3927/health
```

Check `APP_PORT`, mounted-data permissions, disk space, and whether another process owns the port.

### Container cannot write to data

```bash
id -u
id -g
grep -E '^(PUID|PGID)=' .env
ls -ld data
```

Set `PUID` and `PGID` to the Pi account that owns `data`, fix ownership, and recreate the container. Do not run the service as root or make `data` world-writable.

### Port is already in use

Find the listener:

```bash
sudo ss -ltnp | grep ':3927'
```

Stop the conflicting service or choose another `APP_PORT` in `.env`, then recreate the container. An alternate port does not provide authentication.

### Ollama health returns unavailable

Run the checks in **Check Ollama Connectivity**. Distinguish workstation power, Ollama process, model availability, route, and firewall failures before changing configuration.

### Configured model is missing

On Windows:

```powershell
ollama pull qwen2.5:14b
ollama list
```

Alternatively, set `OLLAMA_MODEL` to an installed model and recreate the service.

### API request returns unauthorized

Confirm the client sends `X-API-Key` for mutating endpoints and that the service was recreated after the last key change. Do not print the key while troubleshooting.

### Task remains queued

Check service logs and verify that only one service container/process uses the mounted task directory:

```bash
docker compose -f docker-compose.service.yml --env-file .env ps
docker compose -f docker-compose.service.yml --env-file .env logs --tail 200 ai-job-service
curl --fail http://127.0.0.1:3927/tasks
```

Queued tasks are restored after a normal restart. Do not manually edit task JSON while the service is running.

### Task failed or model output is malformed

Inspect the task record and logs, check `/health/ollama`, and review any retained raw model output. Do not relabel malformed output as a valid fit analysis or add unsupported candidate claims to make a run pass.

### Remote URL is unavailable

First confirm local Pi health. Then inspect the selected access layer:

```bash
curl --fail http://127.0.0.1:3927/health
tailscale status
tailscale serve status
```

For Cloudflare, inspect `cloudflared` service logs and the Access policy. Do not bypass authentication or open router ports as a shortcut.

### Disk space is low

```bash
df -h .
du -sh data backups
docker system df
```

Move verified backups off-device and review unused Docker build cache. Do not delete `data/` or task files to reclaim space without a current backup.

## Recovery Checklist

- [ ] Preserve `.env`, `data/`, and recent logs before invasive changes.
- [ ] Confirm the host, port, mounted path, UID, and GID.
- [ ] Verify local `/health` before troubleshooting remote access.
- [ ] Verify private Ollama access before troubleshooting model output.
- [ ] Keep Ollama and the service off the public internet unless the service is behind a reviewed access layer.
- [ ] Verify restored or regenerated application material manually.
- [ ] Keep LinkedIn capture manual and limited to one current posting.
- [ ] Do not automate Easy Apply or application submission.

## Next Operational Work

The next Phase 3 task should add automated deployment acceptance tests and a documented release/rollback procedure once tagged releases exist.
