# ADR-0008: Telegram long polling as the default transport

**Status:** Accepted — 2026-08-08

## Context

ADR-0002 assumed a public edge: Caddy on 443 with an ACME certificate, and Telegram
delivering updates to a webhook. The owner intends to run HealthCurve on a personal
machine reachable only over Tailscale (ADR-0007), which removes that assumption.

**Telegram webhooks require Telegram's servers to make an inbound HTTPS connection to a
publicly resolvable host with a valid certificate.** Over a private tailnet there is no
such path, so a webhook can never be delivered. The implementation shipped in
`api/routers/telegram.py` is correct and tested, and would work in a public deployment —
but it cannot work on the intended one.

The Bot API offers a second transport: `getUpdates`, a long poll the client initiates.
This is what any self-hosted assistant running on a laptop uses, because it is the only
way to receive Telegram messages without exposing a service.

## Decision

**Long polling is the default transport. The webhook is retained and selectable.**

A `HC_TELEGRAM_MODE` setting takes `polling` (default) or `webhook`.

1. **One update path, two transports.** The allow-list check, update-ID
   deduplication, message-versus-callback dispatch, and the entire draft flow live in
   `integrations.telegram.dispatch` and are identical either way. A transport decides
   only how an update arrives, never what happens to it. This matters because the
   security properties are in the shared path — choosing a transport must not choose a
   weaker set of checks.
2. **The poller runs in the worker**, not the API process. A long poll holds a
   connection open for tens of seconds; that does not belong in a request-serving
   process.
3. **Polling and webhook are mutually exclusive.** Telegram refuses `getUpdates` with
   409 while a webhook is registered, so the poller deletes any registered webhook at
   startup. Running both would double-deliver every message.
4. **The offset is the primary deduplication.** Telegram redelivers everything above
   the last acknowledged `update_id`, so the poller advances the offset only after an
   update is processed. The `ops.telegram_update` table remains as a second guard,
   because an offset lost to a crash would otherwise replay a batch.
5. **Failure is expected and quiet.** Network errors retry with capped exponential
   backoff, without losing the offset. A model outage is already handled downstream.
   The poller must survive the laptop sleeping, the network changing, and Telegram
   being briefly unreachable, because all three are normal on a personal machine.
6. **Nothing about the safety model changes.** Inbound text still creates only a
   draft; a fact still requires confirmation (SAFE-11, SAFE-12).

The webhook's secret-token verification has no equivalent in polling and needs none:
the poller opens an authenticated outbound TLS connection to `api.telegram.org`, so
there is no unauthenticated endpoint for anyone to forge a request to. The relevant
threat (T4) is *reduced* by this choice, not merely relocated.

## Consequences

Positive:

- The bot works on the intended deployment, with no public listener, no inbound
  firewall rule, no certificate, and no DNS.
- The webhook attack surface disappears entirely in the default configuration: there
  is no unauthenticated endpoint to discover, flood, or forge.
- Setup gets substantially simpler — no domain, no tunnel, no `setWebhook` call. The
  setup guide loses its most failure-prone step.
- It works from a laptop behind NAT, on a phone hotspot, or anywhere else.

Negative / costs:

- **Latency**: a message is picked up within the poll interval rather than pushed
  instantly. With a 25-second long poll the practical delay is small, but it is not
  zero.
- **The worker must be running.** With a webhook, a stopped worker still leaves
  Telegram queueing updates for later delivery. With polling, nothing is collected
  while the process is down — though Telegram retains updates for ~24 hours, so a
  restart within that window catches up.
- A persistent outbound connection to Telegram is always open, which is visible to
  anyone watching the network. That reveals that the machine talks to Telegram, not
  what was said.
- Two transports to keep working. Mitigated by the shared dispatch: the transports are
  thin, and the tested logic is common to both.

## Alternatives considered

**Webhook via Tailscale Funnel.** Funnel publishes a tailnet service to the public
internet with a valid certificate, which would make webhooks work. Rejected as the
default: it puts the API back on the public internet, which is the specific thing the
hosting choice was meant to avoid. It remains a supported configuration for anyone
running publicly — hence keeping the webhook path.

**Webhook via a tunnel service** (ngrok, Cloudflare Tunnel). Same objection, plus a
third party in the path of health data, plus an unstable hostname on the free tiers.
Reasonable for a one-off test; documented as such, not as a deployment.

**MTProto user client** (Telethon, Pyrogram). Also outbound-only, and would allow
richer chat features. Rejected: it authenticates as the owner's *personal Telegram
account* rather than a bot, which widens what a compromise reaches from one bot chat to
the owner's entire Telegram presence. The bot allow-list is a cleaner boundary, and the
Bot API is the supported integration surface.

**Polling `getUpdates` on a short interval** rather than a long poll. Simpler, but
wasteful and slower to respond. Long polling is the same call with a `timeout`
parameter and is strictly better.
