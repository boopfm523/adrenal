# Building the web interface

Nothing of the frontend exists yet — no `frontend/` directory, no build step, no served
assets. This is the guide for starting it, written so you can pick it up cold.

The decisions are already made in [ADR-0005](adr/0005-react-spa-frontend.md) and
[ADR-0002](adr/0002-modular-monolith-topology.md). This document is how to execute
them, and what will bite you. The low-fidelity navigation, page-state, category-label,
and accessibility contract is in
[ui-information-architecture.md](ui-information-architecture.md); page implementation
must satisfy both documents.

---

## The stack, and why

**React + TypeScript + Vite**, built to static files and served by Caddy. No SSR, no
Next.js, no meta-framework. A single-owner health record served from a machine in your
house does not need a server-rendering tier, and every layer added is a layer that can
fail while you are unwell.

**Strict TypeScript.** The API returns `Decimal`-precise amounts as strings and two
distinct time fields per event. Those distinctions are exactly what a loose type system
erases.

**No component library initially.** The screens are lists and forms. Reaching for one
early tends to mean fighting its opinions about tables and dates.

### The emergency page is not part of this

`/emergency` is server-rendered HTML with inline CSS and zero JavaScript, and it must
stay that way. It has to work when the SPA's bundle fails to load, when JS is disabled,
and when you are handing your phone to someone else. **Do not migrate it into React.**
It is deliberately excluded in ADR-0005.

---

## Getting started

The foundation now lives in `frontend/`. Install its exact lockfile from the repository
root:

```bash
make setup
```

For frontend-only work, `cd frontend && npm ci --ignore-scripts` is sufficient after
the Python environment already exists. Do not re-run a Vite scaffold over the committed
directory.

Then point Vite's dev server at the API so cookies work in development:

```ts
// frontend/vite.config.ts
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://localhost:8080", changeOrigin: false },
    },
  },
});
```

`changeOrigin: false` matters — the session cookie is host-bound, and rewriting the
origin will silently log you out on every request.

### Production serving

Caddy builds the locked SPA in [deploy/Caddy.Dockerfile](../deploy/Caddy.Dockerfile).
[deploy/Caddyfile](../deploy/Caddyfile) sends `/api/*`, `/emergency`, and
`/emergency/*` to FastAPI and serves all other routes from the built assets. The CSP is
strict —
`script-src 'self'`, no CDNs — so **inline scripts and external fonts will be blocked.**
Vite's default build complies; don't add an inline analytics snippet and wonder why the
page is blank.

---

## Talking to the API

Every route is under `/api/v1`. Read [using-healthcurve.md](using-healthcurve.md) for
the full surface. Generate types rather than hand-writing them:

```bash
make frontend-generate
```

This creates deterministic `frontend/openapi.json` directly from the development
FastAPI application without reading `.env`, then regenerates
`frontend/src/api/schema.d.ts`. Both are committed. `make frontend-check` independently
recreates each artifact and fails on drift, so changing a backend schema without
updating the generated types breaks CI.

### Authentication

`POST /api/v1/auth/login` sets an HTTP-only session cookie and returns a `csrf_token`
in the body.

- **Reads** need only the cookie — send `credentials: "include"`.
- **Writes** additionally need `X-CSRF-Token`. A `403` with "missing or invalid CSRF
  token" means you dropped it.

Keep the CSRF token in memory. Putting it in `localStorage` re-opens the XSS path the
HTTP-only cookie was chosen to close. The central client restores both owner identity
and a fresh in-memory CSRF token through `GET /api/v1/auth/me`; any API `401` clears
TanStack Query's cache and returns the application to the login screen.

### Three rules that will cause bugs if ignored

**1. Amounts are strings, and must stay strings.** `"15.0000"` is `NUMERIC(10,4)` from
the database. `parseFloat` reintroduces the floating-point imprecision the schema was
designed to avoid. Format for display; never round-trip through a `number`.

**2. Every event carries two times.** `occurred_at` is a UTC instant; `local_time` is
the naive wall clock the owner experienced, with `timezone` and `utc_offset_minutes`
alongside. Show `local_time`. Use `occurred_at` only for durations and ordering. Never
call `new Date(local_time)` — the browser will apply *its* timezone to a value that
already has one, and the error only appears when travelling or across a DST change.

**3. Corrections supersede, they do not overwrite.** A corrected dose is a *new* row
pointing at the old one via `supersedes_id`. List endpoints exclude superseded rows by
default; `include_superseded=true` shows the history. A UI that renders both without
distinguishing them will double-count every correction.

---

## What to build, in order

Each of these has a Beads issue under the UI epic — check `bd ready` before starting.

1. **Login and session handling.** Everything else needs it.
2. **Today.** Doses recorded against the approved plan, from
   `/api/v1/doses/plan-comparison`. This is the screen you will actually use daily.
   `missing` slots are derived, not stored — don't try to "complete" them.
3. **Timeline.** `/api/v1/timeline`, paged, filterable by type.
4. **Record a dose / symptom.** Forms posting to `/api/v1/doses` and `/api/v1/symptoms`.
   Time entry is the hard part: default to now, allow editing, and never silently
   resolve an ambiguous time — the API will reject it, and it should.
5. **Medications and plan.** Read-only first. Approval belongs to the CLI for now,
   because it records a physician's decision and shouldn't be a button.
6. **Draft review.** Pending extraction drafts with Confirm / Cancel, mirroring the bot.
7. **Data quality.** Flagged records, ambiguous times, suspected duplicates.
8. **Export and privacy.** What is stored, what leaves, and a working delete.

Stop after 2 if you want something useful quickly. Today plus login is a real tool.

---

## Testing

Run all frontend gates with `make frontend-check`. It runs the contract drift checks,
ESLint (including the `dangerouslySetInnerHTML` ban), Vitest, strict TypeScript, and the
production Vite build. `make audit` also runs the high-severity npm vulnerability gate.

Vitest and React Testing Library. The tests worth writing are the ones covering the
three rules above:

- an amount renders exactly, with no float conversion
- an event during a DST transition renders the wall clock the owner saw
- a corrected dose appears once, not twice

Playwright for one end-to-end path — log in, record a dose, see it on Today — is worth
it. More than that is maintenance you'll regret.

---

## Things that will bite you

**The CSP blocks more than you expect.** No CDN scripts, no Google Fonts, no inline
`<script>`. Bundle everything.

**`npm audit` is part of the security posture,** not noise. The API already runs
`pip-audit` in `make check`; add the JS equivalent when you add the build.

**Do not add analytics, error reporting, or any third-party script.** ADR-0005 is
explicit: a health record must not describe itself to anyone else. Sentry, PostHog and
friends are all out, including their self-hosted "just this once" variants.

**The API is the contract.** If a screen needs data shaped differently, change the API
rather than assembling it from three calls in the browser — the browser is the one
place where a partial failure leaves the record looking wrong.
