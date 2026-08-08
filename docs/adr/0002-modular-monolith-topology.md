# ADR-0002: Modular monolith on Docker Compose behind Caddy

**Status:** Accepted — 2026-08-08

## Context

HealthCurve has one user, several integrations, and a long list of domains
(`identity`, `events`, `medications`, `episodes`, `integrations`, `labs`, `ai`,
`analytics`, `reports`, `operations`). The plan explicitly warns against project
overreach (§17) and prescribes a modular monolith (§4).

The deployment target is a personal subdomain on infrastructure the owner controls.
The threat model (T2) requires that only the public edge is reachable and that
PostgreSQL, Redis, and Ollama are unreachable from the internet.

## Decision

**One API/web application, one worker, one database, one reverse proxy, orchestrated
with Docker Compose.** Domains are Python packages inside a single deployable, not
separate services.

Topology:

```text
                        internet
                           │  443 (+80 redirect/ACME)
                    ┌──────▼──────┐
                    │    caddy    │  the only service publishing ports
                    └──────┬──────┘
        ┌──────────────────┼─────────────────────────┐   private network "hc-internal"
   ┌────▼────┐        ┌────▼────┐              ┌─────▼─────┐
   │   api   │        │ worker  │              │  ollama   │   (see ADR-0003)
   └────┬────┘        └────┬────┘              └───────────┘
        └──────┬───────────┘
        ┌──────▼──────┐   ┌─────────┐
        │  postgres   │   │  redis  │
        └─────────────┘   └─────────┘
```

Rules this ADR fixes:

1. **Only Caddy declares `ports:`.** No other service is published to the host or the
   internet. PostgreSQL, Redis, and Ollama are addressable only by service name on the
   internal network. A release check verifies this from outside the host.
2. **Module boundaries are enforced in code, not by network calls.** Each domain is a
   package with a public interface module; cross-domain imports go through that
   interface. An import-linter contract in CI fails the build on a boundary violation.
   This keeps the option of extraction without paying distributed-systems costs now.
3. **The API process is synchronous-request work only.** Anything slow — provider
   sync, LLM extraction, PDF rendering, backups — runs in the worker (ADR-0004).
4. **The AI worker runs under a separate database role** with no write privileges on
   the `fact` and `plan` schemas (ADR-0001, SAFE-15/16).
5. **Caddy terminates TLS** with automatic certificates and sets HSTS. The application
   never speaks plaintext to the internet and never manages certificates itself.
6. **Environments are separate Compose projects** with distinct secrets and volumes:
   `dev` (local, synthetic data), `staging` (synthetic only, per plan §13), `prod`.
7. **Images are pinned by digest**; containers run as a non-root user; the runtime
   image carries no build toolchain (T6).
8. **Migrations are a deliberate step**, run as a one-shot command against a stopped
   or drained application — never automatically on container start, so a bad migration
   cannot be applied by a restart loop.

## Consequences

Positive:

- One transaction boundary spans the whole domain model, which is what makes SAFE-08
  corrections and the fact/plan/AI partition straightforward to keep consistent.
- The whole system starts with one command locally, which is the precondition for the
  Phase 0 acceptance criterion about fresh-clone setup.
- The attack surface is one public port and one public service.

Negative / costs:

- The api and worker deploy together; there is no independent scaling or independent
  release. Acceptable for one user.
- Module boundaries are a discipline, and disciplines erode. Mitigated by the CI
  import contract — the boundary is checked, not merely intended.
- A single database is a single failure domain. Mitigated by the backup and restore
  work (plan §13) rather than by architecture.

## Alternatives considered

**Microservices per domain.** Rejected outright — the plan names project overreach as
a primary risk, and distributing a single-user application would multiply the
operational surface (service discovery, partial failure, cross-service transactions)
with no benefit at this scale.

**Single container running everything including PostgreSQL.** Simpler still, but it
collapses the process boundary that lets the AI worker run under a restricted database
role, and it complicates backup and upgrade. Rejected.

**Kubernetes.** Wildly disproportionate for one user on one host. Rejected.

**Managed cloud database instead of a container.** Would reduce backup burden but puts
the complete health record in a third party's custody and adds a public network path
to the data, contradicting the threat model's premise of owner-controlled
infrastructure. Rejected; revisit only with encryption-at-rest under owner-held keys.
