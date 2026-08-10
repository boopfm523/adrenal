# Private localhost and Tailscale hosting

This is the operating runbook for HealthCurve on the owner's Mac. HealthCurve is a
single-owner application and is intentionally reachable only in two ways:

1. directly on the Mac at `http://127.0.0.1:8080`; and
2. from an approved Tailscale device through the machine's private `https://*.ts.net`
   URL.

There is no public edge, Funnel, public reverse proxy, router port-forward, or public
HealthCurve domain. Telegram uses outbound long polling. Garmin, weather, and Google
Drive backups are outbound connections.

## Actual topology

```text
approved phone or computer
          |
          | encrypted, authenticated tailnet HTTPS
          v
host Tailscale Serve (`*.ts.net`:443, tailnet only)
          |
          | proxy to loopback HTTP
          v
Caddy (`127.0.0.1:8080`) -> API/web -> PostgreSQL, Redis, workers
                                      -> host Ollama (`127.0.0.1:11434`)
```

Tailscale Serve owns the private HTTPS endpoint and certificate lifecycle. Caddy does
not bind a LAN, Tailscale, or wildcard host address; Docker publishes it only on
loopback. PostgreSQL, Redis, API, workers, and container Ollama publish no host ports.
The host-native Ollama used for Metal acceleration listens only on loopback.

The older `deploy/tailscale.compose.yml` direct-certificate topology remains a tested
alternative, but it is not the owner's normal Mac command and is not required for this
private deployment.

## One-time Tailscale Serve setup

Install and sign in to the macOS Tailscale application, approve the Mac and phone in
the tailnet, and leave Funnel disabled. Configure the private proxy with the bundled
CLI:

```bash
/Applications/Tailscale.app/Contents/MacOS/Tailscale serve --bg \
  http://127.0.0.1:8080
```

Confirm the result without exporting the tailnet configuration:

```bash
/Applications/Tailscale.app/Contents/MacOS/Tailscale serve status
```

The output must say `tailnet only` and show the root path proxying to
`http://127.0.0.1:8080`. Never enable `tailscale funnel` for HealthCurve.

## Private `.env` boundary

The repository-root `.env` is an acceptable secret store for this one-owner,
same-machine deployment. It must remain outside Git and readable only by the owner:

```bash
cd /Users/jeff/Documents/adrenal
chmod 600 .env
stat -f '%Lp' .env
git check-ignore -q .env
```

The mode check must print `600`; `git check-ignore` must exit successfully. Never
display, copy into Beads, commit, screenshot, or send the file. Avoid `env`,
`printenv`, `docker inspect`, and unredacted `docker compose config` in captured
terminals because they can expose injected values.

Database-backed integration-token encryption remains available as defense in depth,
but a separate same-host key system is not required for this private runtime. If the
Mac becomes shared with untrusted local users, the threat model and secret boundary
must be reviewed.

## Start or restart HealthCurve

Use the overlays for the services that are actually configured. The Google Drive
overlay is safe to include only after its private rclone file exists.

```bash
cd /Users/jeff/Documents/adrenal
docker compose \
  -f docker-compose.yml \
  -f deploy/backup.compose.yml \
  -f deploy/google-drive-backup.compose.yml \
  up -d --build
```

Tailscale Serve normally persists independently of the containers. Re-run the
one-time Serve command if `serve status` does not show the loopback proxy. Restarting
containers or Serve does not replace, recreate, or delete the PostgreSQL volume.

This owner-development runtime intentionally uses `HC_ENVIRONMENT=dev`. Interactive
OpenAPI documentation may remain available to the authenticated tailnet owner. That
is not authorization for LAN or public access. Public exposure would require a new
threat model and authentication review.

## Safe verification

These checks print topology and status, not environment values or health records:

```bash
docker compose ps
lsof -nP -iTCP:8080 -sTCP:LISTEN
lsof -nP -iTCP:11434 -sTCP:LISTEN
lsof -nP -iTCP:5432 -sTCP:LISTEN
lsof -nP -iTCP:6379 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
curl -fsS http://127.0.0.1:8080/health/ready
/Applications/Tailscale.app/Contents/MacOS/Tailscale serve status
```

Expected results:

- port 8080 and host-native Ollama port 11434 listen only on `127.0.0.1`;
- ports 5432, 6379, and 8000 have no host listener;
- Compose shows the API and required workers healthy and no internal-service host
  ports;
- the local readiness endpoint returns only `{"status":"ok"}`; and
- Serve reports private tailnet HTTPS proxying to loopback.

Also verify behavior from devices rather than relying only on configuration:

- an approved phone connected to Tailscale can open the private HTTPS URL and must
  still log in to HealthCurve;
- the same phone with Tailscale disconnected cannot reach HealthCurve; and
- the Mac can open `http://127.0.0.1:8080` directly.

HealthCurve intentionally has password-only application login behind this boundary.
Do not add application MFA merely to imitate a public service. Remove a lost device
from Tailscale promptly and use **Settings → Sign out every session**.

## Rotate the Telegram bot token

1. Revoke and issue the token with BotFather.
2. In a private local editor, replace `HC_TELEGRAM_BOT_TOKEN` in `.env`. Do not put the
   token in a shell command, Codex/ChatGPT, Git, Beads, logs, or screenshots.
3. Re-assert owner-only permissions with `chmod 600 .env`.
4. Recreate only the services that read the token:

   ```bash
   cd /Users/jeff/Documents/adrenal
   docker compose \
     -f docker-compose.yml \
     -f deploy/backup.compose.yml \
     -f deploy/google-drive-backup.compose.yml \
     up -d --force-recreate api worker
   ```

5. Run the privacy-safe connectivity check:

   ```bash
   docker compose \
     -f docker-compose.yml \
     -f deploy/backup.compose.yml \
     -f deploy/google-drive-backup.compose.yml \
     run --rm api python -m healthcurve.cli telegram-status
   ```

6. Send `/help` to the bot and confirm the web application still loads. Do not paste
   bot responses containing health data into an issue or log.

Application logs are default-deny redacted, but diagnostics should still prefer
`docker compose ps`, health endpoints, stable reason codes, and counts. Review any log
excerpt locally before sharing it.

## Recovery and boundary changes

An emergency-care plan must remain available independently of this Mac and Tailscale.
HealthCurve availability is not an emergency dependency.

Stop and perform a new security review before enabling Funnel, a public reverse proxy,
router forwarding, a LAN/wildcard listener, a public domain ingress path, multiple
untrusted users, or untrusted local Mac accounts.
