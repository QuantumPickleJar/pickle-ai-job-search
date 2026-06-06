# Raspberry Pi Deployment

## Architecture

This repository contains both the Raspberry Pi service code and the local workflow shared with the Windows workstation. It is not split into a separate Pi repository.

The deployed runtime is split by machine:

```text
Raspberry Pi
  Docker Compose
    ai-job-service
    mounted ./data
        |
        | private LAN or Tailscale
        v
Windows 11 workstation
  Ollama
  NVIDIA GPU
```

The Pi builds and runs `service/` from this repository. The Windows workstation runs Ollama separately. The service calls the workstation through `OLLAMA_BASE_URL`; the browser never calls Ollama directly.

## Requirements

On the Raspberry Pi:

- supported 64-bit Raspberry Pi OS;
- Git;
- Docker Engine;
- Docker Compose plugin;
- private LAN or Tailscale connectivity to the Windows Ollama workstation; and
- enough persistent storage for captured jobs, tasks, profiles, and application workspaces.

Verify the installation:

```bash
uname -m
docker --version
docker compose version
```

An ARM64 Pi should normally report `aarch64`.

## Clone and Configure

Clone the repository on the Pi:

```bash
git clone <YOUR_FORK_URL> ai-job-search
cd ai-job-search
```

Create the ignored local environment file:

```bash
cp .env.example .env
```

Or run the master setup wizard:

```bash
python3 scripts/setup_wizard.py --role service-host
```

Record the Pi user's numeric ownership:

```bash
id -u
id -g
```

Set those values as `PUID` and `PGID` in `.env`. The setup wizard can do this automatically. This lets the non-root container write the bind-mounted `./data` directory without changing the service to run as root.

Create persistent storage:

```bash
mkdir -p data
chmod 700 data
```

Edit `.env` and replace every placeholder. At minimum:

```env
PUID=1000
PGID=1000
APP_HOST=127.0.0.1
APP_PORT=3927
APP_DATA_DIR=/app/data
OLLAMA_BASE_URL=http://192.168.1.50:11434
OLLAMA_MODEL=qwen2.5:14b
APP_API_KEY=<GENERATE_A_STRONG_RANDOM_SECRET>
ENABLE_REMOTE_MODE=false
```

Generate an API key on the Pi without placing it in shell history:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Paste the generated value into the ignored `.env` file. Never commit `.env`.

## Network Modes

### Localhost proxy mode

Recommended for Tailscale Serve or Cloudflare Tunnel:

```env
APP_HOST=127.0.0.1
ENABLE_REMOTE_MODE=true
```

The tunnel or private proxy reaches `http://127.0.0.1:3927`. No LAN client can bypass that proxy through the Pi's normal network address.

See [`docs/remote-access.md`](../docs/remote-access.md) before enabling remote mode.

### Trusted LAN mode

For initial LAN testing, bind to the Pi's reserved private address:

```env
APP_HOST=192.168.1.20
ENABLE_REMOTE_MODE=false
```

Use the actual Pi address. Prefer a DHCP reservation. Do not use router port forwarding.

Binding to `0.0.0.0` is possible but broader than necessary:

```env
APP_HOST=0.0.0.0
```

Use it only when the Pi has multiple trusted private interfaces and its firewall is configured accordingly.

## Configure the Windows Ollama Workstation

Install the configured model on Windows:

```powershell
ollama pull qwen2.5:14b
ollama list
curl.exe http://localhost:11434/api/tags
```

### Make Ollama listen on the private LAN interface

By default Ollama only binds to `127.0.0.1:11434`, so the Pi cannot reach it. Set the `OLLAMA_HOST` environment variable before starting Ollama.

**Option A — for the current PowerShell session only:**

```powershell
$env:OLLAMA_HOST = "0.0.0.0:11434"
ollama serve
```

**Option B — persistent system environment variable (survives reboots):**

```powershell
[System.Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "Machine")
```

Restart the Ollama process (or the Ollama Windows service if it is running as a service) after setting the variable.

Verify Ollama is now listening on the LAN interface by finding the Windows workstation's private IP and testing locally:

```powershell
# Find private LAN address
Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
  Select-Object InterfaceAlias, IPAddress

# Confirm Ollama answers on that address (replace with your actual IP)
curl.exe http://192.168.1.50:11434/api/tags
```

The private IP reported here is the value to use for `OLLAMA_BASE_URL` in the Pi's `.env`.

> **Note:** `0.0.0.0` makes Ollama listen on all interfaces. The Windows Firewall rule below scopes which machines can actually connect.

### Add a Windows Firewall inbound rule

Open PowerShell as Administrator and run the command for your chosen network boundary. Substitute the actual Pi IP or subnet.

**Scope to a single Pi IP (most restrictive — recommended):**

```powershell
New-NetFirewallRule `
  -DisplayName "Ollama - Pi only" `
  -Direction Inbound `
  -Protocol TCP `
  -LocalPort 11434 `
  -RemoteAddress 192.168.1.20 `
  -Action Allow
```

**Scope to a trusted LAN subnet:**

```powershell
New-NetFirewallRule `
  -DisplayName "Ollama - trusted LAN" `
  -Direction Inbound `
  -Protocol TCP `
  -LocalPort 11434 `
  -RemoteAddress 192.168.1.0/24 `
  -Action Allow
```

**Scope to Tailscale only** (if using Tailscale instead of LAN):

```powershell
New-NetFirewallRule `
  -DisplayName "Ollama - Tailscale" `
  -Direction Inbound `
  -Protocol TCP `
  -LocalPort 11434 `
  -RemoteAddress 100.64.0.0/10 `
  -Action Allow
```

Verify the rule exists:

```powershell
Get-NetFirewallRule -DisplayName "Ollama*" |
  Get-NetFirewallAddressFilter |
  Select-Object RemoteAddress
```

Never create a rule with `RemoteAddress Any` for port `11434`, and never forward this port through a router or tunnel.

Use one of these private service configurations in the Pi's `.env`:

```env
# Trusted LAN
OLLAMA_BASE_URL=http://192.168.1.50:11434

# Tailscale
OLLAMA_BASE_URL=http://100.x.y.z:11434
```

Never expose Ollama through:

- router port forwarding;
- Tailscale Funnel;
- Cloudflare Tunnel;
- a public DNS name; or
- browser JavaScript.

Test the private path from the Pi before starting model work:

```bash
curl --fail http://192.168.1.50:11434/api/tags
```

## Validate the Compose Configuration

Render the configuration and inspect it before starting:

```bash
docker compose \
  -f docker-compose.service.yml \
  --env-file .env \
  config
```

Confirm:

- only `ai-job-service` is defined;
- the build context is the repository root;
- the host bind address is the intended private interface;
- `./data` maps to `/app/data`;
- no secret value appears in a committed file; and
- no port maps to Ollama `11434`.

The rendered output includes environment values from `.env`, so do not publish or paste it into public issue reports.

## Build and Start

Build the local ARM image:

```bash
docker compose \
  -f docker-compose.service.yml \
  --env-file .env \
  build
```

Start the service:

```bash
docker compose \
  -f docker-compose.service.yml \
  --env-file .env \
  up -d
```

Or build and start in one command:

```bash
docker compose \
  -f docker-compose.service.yml \
  --env-file .env \
  up -d --build
```

The container:

- restarts unless explicitly stopped;
- runs as `PUID:PGID`;
- mounts `./data` at `/app/data`;
- reads application settings from `.env`;
- publishes `APP_PORT` only on `APP_HOST`; and
- checks `GET /health` every 30 seconds.

`APP_HOST` selects the Pi host interface used for Docker port publication. Compose separately makes the application process listen on `0.0.0.0` inside the container; that internal bind is not a public exposure by itself.

## Verify

Inspect container state:

```bash
docker compose -f docker-compose.service.yml ps
```

The service should become `healthy`.

Follow startup logs:

```bash
docker compose -f docker-compose.service.yml logs -f ai-job-service
```

From the Pi:

```bash
curl --fail http://127.0.0.1:3927/health
curl --fail http://127.0.0.1:3927/health/ollama
```

Open the UI through the configured access path:

```text
http://<PI-LAN-IP>:3927/ui
```

or the Tailscale/Cloudflare HTTPS hostname described in `docs/remote-access.md`.

`/health` confirms the service process is running. `/health/ollama` additionally confirms that the Windows workstation is reachable and the configured model is installed.

## Routine Operations

Pull changes and rebuild:

```bash
git pull --ff-only
docker compose \
  -f docker-compose.service.yml \
  --env-file .env \
  up -d --build
```

Restart:

```bash
docker compose -f docker-compose.service.yml restart ai-job-service
```

Stop without deleting data:

```bash
docker compose -f docker-compose.service.yml down
```

View recent logs:

```bash
docker compose -f docker-compose.service.yml logs --tail 200 ai-job-service
```

Back up persistent data:

```bash
tar -C . -czf "ai-job-data-$(date +%Y%m%d).tar.gz" data
```

Store backups securely because they may contain job descriptions, profile facts, and generated application material.

## Troubleshooting

### Container cannot write to `/app/data`

Confirm the host directory ownership and `.env` values:

```bash
ls -ld data
id -u
id -g
grep -E '^(PUID|PGID)=' .env
```

Set `PUID` and `PGID` to the Pi account that owns `data`, then recreate the container:

```bash
docker compose -f docker-compose.service.yml down
docker compose -f docker-compose.service.yml up -d
```

Do not solve this by running the service as root or making `data` world-writable.

### Service remains unhealthy

Check:

```bash
docker compose -f docker-compose.service.yml ps
docker compose -f docker-compose.service.yml logs ai-job-service
curl -v http://127.0.0.1:3927/health
```

Verify that `APP_PORT` is a valid port and is not already in use.

### Service works but Ollama is unavailable

Check the private Pi-to-Windows path:

```bash
curl -v "${OLLAMA_BASE_URL}/api/tags"
```

If the shell has not loaded `.env`, use the private URL directly. Confirm the workstation is awake, Ollama is running, the configured model is installed, and Windows Firewall allows only the intended private source.

Do not fix this by publishing Ollama to the internet.

### Port is already in use

Choose another service port in `.env`:

```env
APP_PORT=49327
```

Compose maps the same selected port on the host and inside the container. Restart with:

```bash
docker compose \
  -f docker-compose.service.yml \
  --env-file .env \
  up -d --force-recreate
```

### Remote client cannot connect

Confirm `APP_HOST` matches the selected mode:

- `127.0.0.1` for Tailscale Serve or Cloudflare Tunnel;
- the Pi's private IP for LAN access; or
- `0.0.0.0` only with an intentional firewall policy.

Then review [`docs/remote-access.md`](../docs/remote-access.md). Do not open router ports as a troubleshooting shortcut.

## Security and Scope

- The service and its data remain local to the Pi and Windows workstation.
- Ollama remains private and is never exposed directly.
- `.env` and all deployment credentials remain untracked.
- The browser extension remains a manual, one-current-posting capture tool.
- There is no LinkedIn crawling, bulk capture, Easy Apply automation, profile scraping, messaging, or application submission.
- All fit analysis and generated application material requires human review.

## Next Phase 3 Work

The next prompt should add Phase 3 acceptance tests covering:

- Compose configuration rendering;
- container health;
- mounted-data persistence;
- UI and API reachability;
- Ollama-unavailable behavior; and
- the queued processing lifecycle.
