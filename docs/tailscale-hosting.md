# Tailscale-only hosting and TLS

This runbook implements ADR-0007 for a single-owner deployment. HealthCurve stays
off the public internet: no Funnel, public reverse proxy, router forwarding, or
public DNS ingress is used. Telegram continues to use outbound long polling.

## One-time prerequisites

1. Install the standalone Tailscale macOS application, sign in, approve the host,
   and enable MagicDNS and HTTPS certificates in the tailnet console.
2. Apply a tailnet grant/ACL that permits TCP 443 to this machine only from the
   owner's user or a dedicated owner-device group. Do not grant all tailnet members.
3. Record the machine's concrete Tailscale IPv4 and full MagicDNS `*.ts.net` name in
   the uncommitted `.env`. Never put either value in issue notes or logs.
4. Create a dedicated certificate directory owned by the service operator, mode
   `0700`. The private key inside it must be mode `0600`.

Set these uncommitted values:

```dotenv
HC_CADDY_BIND_IP=100.x.y.z
HC_CADDY_HOST_PORT=443
HC_CADDY_CONTAINER_PORT=443
HC_TAILSCALE_DNS_NAME=machine.example-tailnet.ts.net
HC_TAILSCALE_CERT_DIR=/absolute/private/path/tailscale-certs
```

## Issue and renew the certificate

Certificate issuance publishes the machine and tailnet DNS labels to public
certificate-transparency logs. Obtain the owner's explicit approval before the first
issuance. Run the bundled Tailscale CLI from inside the protected certificate
directory, using the full MagicDNS name:

```bash
/Applications/Tailscale.app/Contents/MacOS/Tailscale cert \
  --cert-file healthcurve.crt \
  --key-file healthcurve.key \
  machine.example-tailnet.ts.net
chmod 600 healthcurve.key
```

File-output certificates are not renewed automatically. Renew before expiry with the
same command, then reload Caddy. Certificate expiry monitoring is required before
release; never print or copy the private key.

## Validate and start

Use an empty environment so Compose never prints or inherits credentials during a
configuration-only check. Supply placeholders only for required fields:

```bash
env -i PATH="$PATH" \
  POSTGRES_PASSWORD=validation-only \
  POSTGRES_AI_PASSWORD=validation-only \
  HC_CADDY_BIND_IP=100.64.0.1 \
  HC_CADDY_HOST_PORT=443 \
  HC_CADDY_CONTAINER_PORT=443 \
  HC_TAILSCALE_DNS_NAME=machine.example-tailnet.ts.net \
  HC_TAILSCALE_CERT_DIR=/tmp/healthcurve-validation-certs \
  docker compose -f docker-compose.yml -f deploy/tailscale.compose.yml \
  config --quiet
```

Start production only after real secrets, backups, monitoring, the owner-only
tailnet policy, and the password login are ready:

```bash
docker compose -f docker-compose.yml -f deploy/tailscale.compose.yml up -d --build
```

## Release verification

Record only pass/fail evidence—never IPs, DNS suffixes, identities, credentials, or
health data.

- On the host, confirm only Caddy listens on the specific Tailscale address at 443;
  PostgreSQL, Redis, API, Ollama, and workers publish nothing.
- From an approved tailnet device, open the MagicDNS HTTPS URL and confirm the browser
  trusts the certificate, login is still required, and `/health/ready` succeeds.
- From an unauthorized tailnet identity, confirm the grant/ACL denies TCP 443.
- From a device with Tailscale disconnected (and cellular or another outside
  network), confirm the hostname/service cannot be reached.
- Inspect the router and confirm no port forward targets this host. Confirm Funnel is
  disabled.

HealthCurve intentionally offers no application MFA/passkey option for this private,
single-owner deployment. Public exposure is prohibited and requires a new security and
authentication review before any deployment change.

An emergency-care plan must remain available independently of this host and tailnet.
