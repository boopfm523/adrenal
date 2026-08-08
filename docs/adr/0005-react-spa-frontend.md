# ADR-0005: React + TypeScript + Vite frontend

**Status:** Accepted — 2026-08-08

## Context

The plan suggests "React, TypeScript, Vite, TanStack Query, and ECharts or Plotly",
and explicitly allows "Jinja/HTMX is a valid simplification if chosen in an
architecture decision record" (§5). This ADR is that decision point.

What the UI must actually do (plan §10):

- Interactive charts with overlays, and an accessible table/text alternative for each.
- A dense, filterable unified timeline with source, timezone, confirmation, and
  correction indicators.
- A confirm/edit/cancel draft flow with per-field confidence.
- A report builder with range and section selection and a live preview.
- Mobile-first responsive layout, keyboard navigation, screen-reader labels, visible
  focus, large touch targets.
- **An emergency page that renders with AI, integrations, and background jobs all
  down** (SAFE-21).

## Decision

**React + TypeScript + Vite + TanStack Query, with ECharts for charts** — with one
firm exception.

**The emergency page is server-rendered HTML with no client-side framework
requirement.** It is served directly by the API from the approved-instruction records,
styled with plain CSS, and it functions with JavaScript disabled and every other
service down. SAFE-21 is a hard requirement, and making the safety-critical page
depend on a JavaScript bundle loading successfully is exactly the coupling that
requirement exists to prevent. The SPA links to it; it does not own it.

Additional rules:

1. **TypeScript strict mode**, with API types generated from the OpenAPI schema so a
   backend contract change breaks the frontend build rather than production.
2. **TanStack Query owns server state.** No hand-rolled fetch-and-store. Cache
   settings must respect `Cache-Control: no-store` on health-bearing responses (T7).
3. **The category discriminator drives rendering.** Fact, plan, and AI have distinct
   components with distinct non-color affordances (SAFE-02, SAFE-04, SAFE-24). There is
   no generic "record" component that renders all three the same way.
4. **Every chart ships an accessible alternative** — a data table with the same
   numbers, plus rendered metric definition, timezone, and missingness (SAFE-25,
   SAFE-26, SAFE-27). A chart component that cannot produce its table does not ship.
5. **All rendered text is treated as untrusted.** Diary content, imported notes, and
   LLM output are rendered as text, never as HTML. `dangerouslySetInnerHTML` is
   banned by lint rule.
6. **No health data in URLs, page titles, or notification text** (T7).
7. **No third-party analytics, fonts, or CDN assets.** Everything is served from the
   application's own origin — a health record must not emit requests describing itself
   to anyone.
8. **Accessibility is a gate, not a phase.** Automated audit plus a keyboard-only
   journey run in CI.

## Consequences

Positive:

- The interaction-heavy surfaces (draft confirmation, chart overlays, report builder)
  get the model they need, and typed API contracts catch drift at build time.
- The safety-critical page is deliberately excluded from the framework's failure
  modes — no bundle, no hydration, no client routing between the owner and their
  emergency instructions.
- Generated types make SAFE-02's category discriminator a compile-time obligation.

Negative / costs:

- Two rendering paths to maintain and test (SPA plus the server-rendered emergency
  page). Accepted deliberately; the duplication is small and buys independence.
- A build toolchain, a Node dependency tree, and a larger dependency-compromise surface
  (T6). Mitigated by lockfile pinning, CI scanning, no CDN assets, and keeping the
  dependency list short.
- SPA accessibility (focus management, live regions, route announcements) takes
  deliberate work that server-rendered HTML gets closer to for free. Mitigated by the
  CI accessibility gate.
- Charting libraries are heavy. ECharts is loaded only on routes that chart.

## Alternatives considered

**Jinja + HTMX for everything.** Genuinely tempting: simpler, no build step, better
default accessibility, smaller attack surface, and it would make SAFE-21 trivial
everywhere. Rejected because the report builder, the multi-series chart overlays, and
the per-field draft confirmation flow are stateful client interactions that HTMX would
push back to the server in a way that degrades on mobile — and because typed
end-to-end API contracts are a real safety asset for a domain where a mislabeled
category is a clinical problem. The decision is close enough that it should be
revisited if SPA accessibility or bundle weight becomes a recurring source of defects.

**Next.js or another SSR meta-framework.** Adds a Node runtime to the production
deployment and a second server to secure and monitor, for SEO and first-paint benefits
that a private single-user record does not need. Rejected.

**Plotly instead of ECharts.** Comparable capability; ECharts chosen for smaller
bundles and better control over accessible alternatives. Low-stakes and reversible.

**Native mobile app.** Explicitly a non-goal until web and Telegram are dependable
(plan §1).
