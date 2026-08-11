# ADR-0017: Host-native Ollama is the owner runtime default

**Status:** Accepted — 2026-08-11

## Context

ADR-0003 allowed either a co-located Compose service or a private remote model and
originally made the Compose service the development default. The actual HealthCurve
host is an Apple Silicon Mac. Ollama inside Docker Desktop cannot use Metal, duplicates
roughly 19–39 GB of model storage, and is substantially less suitable than the native
service already installed on the same owner-controlled machine. The owner explicitly
does not want the large model running in Docker.

The health-data privacy boundary is unchanged: Ollama is unauthenticated and must not
be exposed to the LAN, tailnet, or public internet. Model unavailability must remain a
safe, visible degradation rather than preventing deterministic recording or emergency
information.

## Decision

The normal Mac runtime runs Ollama natively, bound to host loopback, and API/worker
containers reach it at `http://host.docker.internal:11434` through Docker Desktop's
private host bridge. `HC_OLLAMA_BASE_URL` remains configurable and retains the strict
private-address startup validation from ADR-0003.

The `ollama` Compose service remains only as an explicit compatibility topology under
the `container-ollama` profile. Ordinary `docker compose up` and the documented owner
startup do not start it. The optional service publishes no port and stays on the
internal network. Opting in requires both the profile and an endpoint override:

```bash
HC_OLLAMA_BASE_URL=http://ollama:11434 \
  docker compose --profile container-ollama up -d ollama api worker
```

Changing the default does not delete any existing Docker container, image, named
volume, or model blob. Operators may stop a redundant container while retaining its
volume. No automatic cleanup or migration copies model data.

Every deterministic command, web form, Garmin sync, emergency surface, and stored fact
continues to work when native Ollama or a configured model is unavailable. Model-backed
draft creation exposes the existing safe unavailable/fallback state and never saves an
invented fact.

## Consequences

- Apple Metal acceleration and the already-hosted model are used without a duplicate
  large Docker runtime.
- Base Compose startup is smaller and no longer implies that Ollama is a required
  container.
- Docker Desktop's private host gateway is now part of the owner runtime. A different
  operating system must configure an equivalent private endpoint explicitly.
- The co-located topology remains testable and reversible, but cannot start
  accidentally during ordinary owner operations.

## Alternatives considered

**Keep container Ollama as the default.** Rejected because it ignores the owner's
explicit requirement, cannot use Metal on Docker Desktop, and duplicates large model
storage.

**Remove the Compose service and volume declaration.** Rejected because that would
make recovery harder and could encourage destructive cleanup. An explicit profile
preserves a supported compatibility path and existing data.

**Publish native Ollama to a LAN or tailnet interface.** Rejected because Ollama lacks
the authentication required for sensitive health-model traffic. Host loopback plus the
Docker Desktop bridge keeps the listener private.
