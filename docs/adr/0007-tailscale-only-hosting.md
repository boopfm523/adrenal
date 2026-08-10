# ADR-0007: Tailscale-only hosting with no public edge

**Status:** Accepted — 2026-08-09

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

The production path is:

```text
owner's approved device
        |
        | encrypted, authenticated tailnet
        v
host Tailscale address:443
        |
        v
      Caddy  --->  API/web  --->  PostgreSQL / Redis / worker
                                      |
                                      +---- private Ollama path (ADR-0003)
```

1. **Listener boundary.** Caddy is the only Compose service allowed to publish a
   port. In production its HTTPS listener binds to the host's specific Tailscale IPv4
   address, not `0.0.0.0`, `::`, a LAN address, or an unqualified port. Development
   may bind Caddy to loopback. PostgreSQL, Redis, API, workers, and Ollama never publish
   host ports.
2. **TLS.** The preferred certificate is issued with `tailscale cert` for the host's
   MagicDNS `*.ts.net` name and mounted read-only into Caddy. Renewal is an explicit,
   monitored host operation. An owner-operated internal CA is the fallback when
   Tailscale HTTPS certificates are unavailable; its root is installed only on
   approved devices. Public ACME HTTP-01, public DNS validation credentials, and port
   80 are not used.
3. **Access policy.** Tailnet policy grants TCP 443 only to the owner's named user or
   dedicated device group and only for this host. Tailnet membership is necessary but
   not sufficient when an ACL/grant can narrow access. Device approval, tailnet MFA,
   key expiry, and prompt removal of lost devices are part of the production runbook.
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
   published by a service other than Caddy and rejects Caddy bindings that omit a host
   IP or use anything except loopback or `100.64.0.0/10`. Before release, verify from:
   (a) a device outside the tailnet, where no HealthCurve port responds; (b) an
   unauthorized tailnet identity, where policy denies 443; and (c) an authorized
   device, where HTTPS succeeds with the expected name and certificate. Also inspect
   the host socket table and router configuration. Evidence must contain no secrets or
   health data.

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
- Certificate issuance and renewal are no longer automatic inside Caddy. Expiry
  monitoring and careful private-key permissions become operational requirements.
- Availability now depends on the host and Tailscale control/data paths. This is an
  acceptable trade for a personal tracker; emergency instructions must not depend on
  HealthCurve availability.
- Remote physician access cannot be provided by sending a public link. Reports must be
  exported deliberately, or the physician must be explicitly and temporarily admitted
  under a separately reviewed policy.

## Consequences

The production implementation needs a host Tailscale installation, a narrowly scoped
tailnet policy, a fixed way to inject the host's Tailscale address, certificate renewal
and expiry monitoring, and three-vantage release checks. Those controls are delivered
by the production hosting and monitoring issues; this ADR does not claim they are
already deployed.

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
