# Secure Remote Access

## Purpose

This guide describes supported ways to reach the Raspberry Pi hosted AI Job Search Service from another device while keeping job data, profile facts, generated documents, and the Windows Ollama endpoint private.

The recommended order is:

1. LAN-only access for initial testing.
2. Tailscale private access for routine remote use.
3. Cloudflare Tunnel protected by Cloudflare Access when a browser-friendly public hostname is required.
4. Tailscale Funnel only after whole-service authentication is added and reviewed.

The remote browser connects to the Pi service. The Pi service connects separately to Ollama through `OLLAMA_BASE_URL`.

```text
Authorized browser
        |
        | LAN, tailnet, or Access-protected HTTPS
        v
Raspberry Pi: AI Job Search Service
        |
        | private LAN or tailnet only
        v
Windows 11 workstation: Ollama
```

Never route a public hostname, tunnel, Funnel, or router port forward to Ollama port `11434`.

## Security Baseline

Before enabling access beyond localhost:

- Set a strong, unique `APP_API_KEY`.
- Set `ENABLE_REMOTE_MODE=true`.
- Keep service data in a mounted directory outside the container image.
- Restrict the Pi service to the intended interface or localhost proxy target.
- Restrict Windows Firewall access to Ollama to the Pi, trusted LAN, or tailnet.
- Verify `/health` and `/health/ollama` without publishing their upstream details.
- Keep `.env`, tunnel credentials, auth keys, and tokens out of Git.
- Require human review of every generated fit analysis and application document.

`APP_API_KEY` currently protects mutating API and UI actions. It is defense in depth, not complete authentication for all read-only pages and endpoints. A public deployment therefore requires an access layer that authenticates every request.

The service does not crawl LinkedIn, capture jobs in bulk, automate Easy Apply, scrape profiles or contacts, send messages, or submit applications.

## Option 1: LAN-Only

Use LAN-only access while setting up the Pi and Windows workstation. This is the simplest deployment, but any device allowed onto the trusted LAN may be able to reach the published service port.

### Example environment

```env
APP_HOST=192.168.1.20
APP_PORT=3927
APP_DATA_DIR=/app/data
OLLAMA_BASE_URL=http://192.168.1.50:11434
OLLAMA_MODEL=qwen2.5:14b
APP_API_KEY=<GENERATE_A_STRONG_RANDOM_VALUE>
ENABLE_REMOTE_MODE=false
```

`APP_HOST` controls the Pi interface used by the Compose port publication. Compose separately binds the process inside the container.

```yaml
ports:
  - "192.168.1.20:3927:3927"
```

Replace the example addresses with reserved private addresses for the Pi and Windows workstation.

### Verify

From another trusted LAN device:

```bash
curl --fail http://192.168.1.20:3927/health
```

Open:

```text
http://192.168.1.20:3927/ui
```

Do not forward `3927` or `11434` through the router for this mode.

## Option 2: Tailscale Private Access

Tailscale private access is the recommended default. Join the Pi, Windows workstation, and authorized client devices to the same tailnet, then limit access with Tailscale grants or ACLs.

Tailscale Serve can provide a tailnet-only HTTPS URL and proxy it to a service listening on the Pi's localhost interface. Traffic remains limited to users or devices allowed by the tailnet policy.

### Example environment

```env
APP_HOST=127.0.0.1
APP_PORT=3927
APP_DATA_DIR=/app/data
OLLAMA_BASE_URL=http://100.101.102.50:11434
OLLAMA_MODEL=qwen2.5:14b
APP_API_KEY=<GENERATE_A_STRONG_RANDOM_VALUE>
ENABLE_REMOTE_MODE=true
```

Use the Windows workstation's Tailscale address or MagicDNS name for `OLLAMA_BASE_URL`. Do not use the Pi service's public name or a Funnel URL.

Bind the container only to Pi localhost when Tailscale Serve is the sole ingress:

```yaml
ports:
  - "127.0.0.1:3927:3927"
```

### Start private HTTPS access

On the Pi:

```bash
sudo tailscale up
tailscale status
sudo tailscale serve --bg 3927
sudo tailscale serve status
```

The status output reports a tailnet-only HTTPS URL similar to:

```text
https://raspberry-pi.<tailnet-name>.ts.net
```

Test it from an authorized tailnet device:

```bash
curl --fail https://raspberry-pi.<tailnet-name>.ts.net/health
```

To remove the Serve configuration:

```bash
sudo tailscale serve reset
```

### Tailnet policy

Limit the Pi service to the user, group, or tagged devices that need it. Do not rely on tailnet membership alone when the tailnet contains unrelated users or devices.

Tailscale Serve adds identity headers to proxied requests, but this application does not currently use those headers for authorization. Keep `APP_API_KEY` enabled for mutating operations and keep the backend bound to localhost so clients cannot bypass Serve.

No router port forwarding is required.

## Option 3: Tailscale Funnel

Tailscale Funnel provides a public HTTPS URL that anyone on the internet can reach. It is not the recommended default.

Important differences from Tailscale Serve:

- Funnel is public, not tailnet-only.
- Funnel does not provide Serve identity headers.
- Funnel currently accepts public HTTPS only on its supported ports.
- Funnel and Serve cannot use the same HTTPS port at the same time; the most recently configured mode determines whether that port is private or public.

The current service does not require `APP_API_KEY` for read-only UI and API routes. Therefore:

> Do not enable Funnel for this service until a reviewed authentication layer protects every route, not only mutating requests.

After whole-service authentication, rate limiting, and request logging are implemented, a temporary Funnel could be created with:

```bash
sudo tailscale funnel --bg 3927
sudo tailscale funnel status
```

To remove all Funnel configuration:

```bash
sudo tailscale funnel reset
```

Before and after every Funnel change, inspect both modes:

```bash
sudo tailscale serve status
sudo tailscale funnel status
```

Never Funnel:

- `11434`;
- the Windows workstation;
- a generic Ollama proxy;
- an unauthenticated service instance; or
- a service containing real profile or application data during testing.

## Option 4: Cloudflare Tunnel With Access

Cloudflare Tunnel is appropriate when a stable public HTTPS hostname is needed without opening an inbound router port. The `cloudflared` connector initiates outbound connections from the Pi to Cloudflare.

Cloudflare Tunnel alone is not authentication. Create a Cloudflare Access self-hosted application and an explicit Allow policy for the service hostname. Do not add a Bypass policy.

### Example environment

```env
APP_HOST=127.0.0.1
APP_PORT=3927
APP_DATA_DIR=/app/data
OLLAMA_BASE_URL=http://192.168.1.50:11434
OLLAMA_MODEL=qwen2.5:14b
APP_API_KEY=<GENERATE_A_STRONG_RANDOM_VALUE>
ENABLE_REMOTE_MODE=true
```

The Pi may reach Ollama through a private LAN address or Tailscale address. The Cloudflare hostname must map only to the Pi application service.

Bind the service to Pi localhost so the Access-protected tunnel is the only remote ingress:

```yaml
ports:
  - "127.0.0.1:3927:3927"
```

### Create a locally managed tunnel

The following commands are examples. Cloudflare currently recommends remotely managed tunnels for most deployments, but a locally managed configuration makes the exact origin mapping visible for this standalone Pi deployment.

```bash
cloudflared tunnel login
cloudflared tunnel create ai-job-service
cloudflared tunnel list
```

Create `~/.cloudflared/config.yml` on the Pi:

```yaml
tunnel: <TUNNEL-UUID>
credentials-file: /home/<PI-USER>/.cloudflared/<TUNNEL-UUID>.json

ingress:
  - hostname: jobs.example.com
    service: http://127.0.0.1:3927
  - service: http_status:404
```

The final catch-all rule prevents unmatched hostnames from reaching another local service.

Validate the configuration:

```bash
cloudflared tunnel ingress validate
cloudflared tunnel ingress rule https://jobs.example.com
```

### Configure Access before use

In Cloudflare Zero Trust:

1. Add a self-hosted Access application for `jobs.example.com`.
2. Add an explicit Allow policy for only the required identity, email, group, or device posture.
3. Set a short, appropriate session duration.
4. Confirm there is no Bypass policy.
5. Test denial with an unauthorized browser profile before using real data.

Then create the DNS route and start the tunnel:

```bash
cloudflared tunnel route dns ai-job-service jobs.example.com
cloudflared tunnel --config ~/.cloudflared/config.yml run ai-job-service
```

After interactive verification, install `cloudflared` as a boot service using the current Cloudflare instructions for Raspberry Pi OS.

### Verify

An unauthorized browser should receive the Access login or denial page, never the service dashboard.

After successful Access authentication:

```bash
curl --fail https://jobs.example.com/health
```

Interactive browser verification is preferable for identity-protected routes because a plain `curl` request may be redirected to Access authentication.

Do not create another host port, DNS record, or tunnel ingress rule that bypasses Access.

## Ollama Network Boundary

Ollama is a private backend dependency, not a remote client endpoint.

Allowed examples:

```env
# Pi and Windows workstation on a trusted LAN
OLLAMA_BASE_URL=http://192.168.1.50:11434

# Pi and Windows workstation on the same tailnet
OLLAMA_BASE_URL=http://100.101.102.50:11434
```

Prohibited examples:

```env
OLLAMA_BASE_URL=https://ollama.example.com
OLLAMA_BASE_URL=https://<device>.<tailnet-name>.ts.net
```

Do not:

- create a Cloudflare Tunnel hostname for Ollama;
- enable Tailscale Funnel for Ollama;
- forward router port `11434`;
- expose `/api/chat` through the Pi service; or
- allow the browser or extension to call Ollama directly.

Restrict Windows Firewall port `11434` to the Pi address, a narrow trusted subnet, or the relevant tailnet interface.

## Validation Checklist

Before treating remote access as ready:

- [ ] The Pi service is reachable only through the selected ingress path.
- [ ] `APP_API_KEY` is set to a non-placeholder secret.
- [ ] `ENABLE_REMOTE_MODE=true` only after the ingress policy is active.
- [ ] An unauthorized client cannot read `/ui`, `/jobs`, `/applications`, or `/tasks`.
- [ ] An authorized client can reach `/health` and the dashboard.
- [ ] Ollama is reachable from the Pi through a private address.
- [ ] Ollama is not reachable from the public internet.
- [ ] No router forwarding targets `3927` or `11434`.
- [ ] `.env`, Tailscale auth keys, Cloudflare tokens, certificates, and tunnel credentials are not tracked by Git.
- [ ] The LinkedIn clipper remains a manual, one-current-job capture tool.
- [ ] Application submission and generated-document review remain manual.

## Troubleshooting

### Tailscale Serve URL is unavailable

Check:

```bash
tailscale status
tailscale serve status
```

Confirm the client is in the tailnet and permitted by the tailnet policy. Confirm the service responds locally on the Pi:

```bash
curl --fail http://127.0.0.1:3927/health
```

### Funnel unexpectedly made the service public

Disable it immediately:

```bash
sudo tailscale funnel reset
sudo tailscale serve status
sudo tailscale funnel status
```

Remember that Serve and Funnel configuration on the same HTTPS port are mutually exclusive.

### Cloudflare hostname bypasses authentication

Stop the tunnel, remove or disable the DNS route, and review the Access application and policies. A Tunnel hostname without an Access policy is publicly reachable.

### Service works but Ollama health fails

Remote access to the Pi and private access from the Pi to Windows are separate network paths. Verify `OLLAMA_BASE_URL`, Windows Firewall, workstation power state, and Ollama's private-interface listener. Do not fix this by exposing Ollama publicly.

## Assumptions

- The deployment is single-user or single-household.
- The Raspberry Pi runs a supported 64-bit Raspberry Pi OS.
- Docker publishes the application on Pi port `3927`.
- The Windows workstation runs Ollama and may be offline when capture occurs.
- The Pi and Windows workstation share a private LAN or tailnet path.
- Public read access is unacceptable because jobs, profile facts, and generated documents are private.
- Provider CLI syntax and product limits may change; verify commands against the linked official documentation before production use.

## Official References

- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)
- [Tailscale Serve CLI](https://tailscale.com/docs/reference/tailscale-cli/serve)
- [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel)
- [Tailscale Funnel CLI](https://tailscale.com/docs/reference/tailscale-cli/funnel)
- [Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/)
- [Cloudflare locally managed tunnel setup](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-local-tunnel/)
- [Cloudflare Tunnel configuration files](https://developers.cloudflare.com/tunnel/advanced/local-management/configuration-file/)
- [Cloudflare Access self-hosted applications](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/choose-application-type/)
- [Cloudflare Access policies](https://developers.cloudflare.com/cloudflare-one/policies/access/)
