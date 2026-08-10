# ADR-0007: Tailscale-only hosting with no public edge

**Status:** Accepted — 2026-08-09; host-Serve topology amended — 2026-08-10

## Context

HealthCurve holds a longitudinal health record for one owner. The intended production
host is a personal machine, and the owner does not need anonymous or public access.
ADR-0002 originally placed Caddy on public ports 80 and 443 and used public ACME. That
creates an unnecessary internet login surface and conflicts with Telegram long polling
(ADR-0008), which was selected specifically to avoid inbound internet traffic.

Tailscale gives authenticated devices a private address in `100.64.0.0/10`, device and
user identity at the network boundary, MagicDNS, access-control policy, and HTTPS
certificates for eligible `*.ts.net` names. A Tailscale certificate proves the tailnet
name but does not make the service public. Certificate private keys remain sensitive
host secrets.

## Decision

**Production is reachable only from the owner's tailnet. There is no public edge,
Tailscale Funnel, public reverse proxy, or router port-forward.**

The owner's Mac uses Tailscale Serve as the private HTTPS terminator. The operating
path is:

```text
owner's approved device
        |
        | encrypted, authenticated tailnet
        v
host Tailscale Serve:443 (`tailnet only`)
        |
        | proxy to `127.0.0.1:8080`
        v
loopback Caddy  --->  API/web  --->  PostgreSQL / Redis / worker
                                      |
                                      +---- loopback Ollama path (ADR-0003)
```

1. **Listener boundary.** Docker publishes only Caddy and binds it to
   `127.0.0.1:8080`. Tailscale Serve is the only tailnet listener and proxies private
   HTTPS to that loopback endpoint. PostgreSQL, Redis, API, workers, and container
   Ollama publish no host ports. Host-native Ollama binds only to loopback. No service
   binds `0.0.0.0`, `::`, or a LAN address.
2. **TLS.** Tailscale Serve manages the eligible MagicDNS `*.ts.net` certificate and
   terminates HTTPS. HealthCurve does not copy or mount its private key. Public ACME,
   public DNS validation credentials, and port 80 are not used. The repository's
   direct-certificate Compose overlay remains a tested alternative, not the normal Mac
   operating path.
3. **Access policy.** Serve is configured `tailnet only`; Funnel remains disabled.
   Tailnet policy and device approval restrict reachability to the owner's approved
   devices. Prompt removal of lost devices and HealthCurve's **Sign out every
   session** control are part of the runbook.
4. **Application authentication remains required.** The owner explicitly selected a
   password-only HealthCurve login for this single-user, Tailscale-only deployment.
   Secure sessions, CSRF protection, login throttling, audit events, password
   reauthentication, and session revocation remain required. HealthCurve exposes no
   MFA/passkey enrollment or login route. A compromised approved device can still
   reach the login page.
5. **No public ingress dependencies.** Telegram uses outbound long polling
   (ADR-0008). Garmin and weather integrations are outbound. Backups leave through an
   outbound, create-only interface. HealthCurve must not enable Funnel merely to make
   an integration easier.
6. **Verification replaces the old public-port check.** CI rejects any Compose port
   published by a service other than Caddy and rejects wildcard or LAN bindings. The
   live check must show Serve as `tailnet only`, Caddy on loopback, internal services
   unpublished, successful HTTPS from an approved tailnet device, and failure from the
   same device with Tailscale disconnected. Evidence must contain no secrets, tailnet
   configuration export, or health data.

This decision supersedes ADR-0002 only where it described an internet edge, public
80/443, ACME HTTP challenges, or an external test expecting public 443. Its modular
monolith and internal service topology remain accepted.

The password-only decision is inseparable from the private network boundary. Public
exposure—including Funnel, a public reverse proxy, public port forwarding, or a hosted
tunnel—is a release blocker. Any proposal for public access requires a new threat
model and authentication review before deployment; this ADR does not authorize it.

## Threat-model effects

Stronger:

- Internet credential stuffing, public endpoint discovery, generic scanning, webhook
  forgery, and denial-of-service traffic lose a network path.
- Database, cache, Ollama, and application ports remain unrouteable from both the
  internet and the host's LAN; an accidental wildcard Compose binding is a CI failure.
- A public DNS name and public certificate-transparency entry are no longer required
  for the service.

Weaker or newly important:

- The tailnet control plane, owner account, ACL/grant policy, node keys, and approved
  devices become part of the trusted computing base.
- A stolen, unlocked, or compromised approved device has a direct network path to the
  application, making application authentication and session revocation essential.
- Tailscale Serve and its managed HTTPS lifecycle become part of the trusted host
  configuration; an accidental Funnel or Serve reset must be detected by the
  privacy-safe status check.
- Availability now depends on the host and Tailscale control/data paths. This is an
  acceptable trade for a personal tracker; emergency instructions must not depend on
  HealthCurve availability.
- Remote physician access cannot be provided by sending a public link. Reports must be
  exported deliberately, or the physician must be explicitly and temporarily admitted
  under a separately reviewed policy.

## Consequences

The private implementation needs the host Tailscale application, a tailnet-only Serve
mapping to loopback Caddy, approved owner devices, no router forwarding, and simple
listener/status checks. The repository-root `.env`, kept out of Git and mode `0600`,
is a proportionate secret boundary for this one-owner, same-Mac deployment. Separate
database-backed token encryption remains optional defense in depth rather than a
private-runtime gate.

The owner-development runtime may retain development OpenAPI documentation on the
trusted tailnet. This does not authorize LAN, public, Funnel, or multi-user exposure;
any such boundary change requires a new threat model and authentication review.

The personal domain can remain useful for documentation or a redirect, but it is not
a HealthCurve ingress path. The application URL is the private MagicDNS name.

## Alternatives considered

**Public Caddy on 443.** Familiar and easy to share, but exposes an authentication
surface continuously for a single user. Rejected.

**Tailscale Funnel.** Provides public HTTPS while retaining Tailscale tooling. It is
still a public edge and defeats the principal security property. Rejected.

**Cloudflare Tunnel or another hosted tunnel.** Avoids router configuration but adds a
third party to the request path and still creates a public endpoint. Rejected.

**Plain HTTP over Tailscale.** Wire traffic is encrypted, but browsers and application
security features should see a secure origin, and TLS provides defense in depth against
local proxy mistakes. Rejected.

**Internal CA only.** Valid fallback, but distributing and rotating a private trust
root is more operational work than using a tailnet certificate. Not the default.
