# ADR-0029: One-way public static HealthCurve mirror

**Status:** Accepted — 2026-08-24; amended by ADR-0032 — 2026-08-28

## Context

ADR-0007 keeps the authenticated HealthCurve application, API, database, workers,
credentials, and local model off the public internet. The owner now explicitly wants
an anonymous public demonstration at `https://jeffellin.com/healthcurve/` and has
explicitly authorized publication of selected real curve data there. Making the
private application public would invalidate its password-only threat model. Copying a
complete private API response would disclose identifiers, provenance, notes, and data
that the curve does not need.

The public result must be useful without becoming another application backend. A
visitor needs the current interactive curve, its accessible alternatives, and a
calendar of complete published days. Visitors do not need authentication, data entry,
an API, or any path back into the private runtime.

## Decision

HealthCurve may publish a second, deliberately smaller product boundary: a generated
static site containing HTML, CSS, JavaScript, a non-sensitive manifest, and one
allow-listed JSON projection per eligible local day. This is additive to ADR-0007;
the private application and all of its listeners remain Tailscale-only.

### Publication boundary

The flow is one way:

```text
private PostgreSQL + deterministic services
                 |
                 | local owner-scoped read, explicit projection
                 v
       ignored staging directory
                 |
                 | validation + atomic replacement
                 v
       static HTML/CSS/JS/JSON bundle
                 |
                 | forced write-only rrsync key
                 v
 jeffellin.com/healthcurve/ (anonymous, read-only)
```

The public host receives no Python, database, provider token, owner credential,
private API URL, source document, server-side session, form handler, telemetry, or
third-party script. Browser code can fetch only files under the static site path.

### Explicit public allow-list

Per-day files may contain only values required by the supported HealthCurve chart and
its accessible summaries:

- local calendar date, IANA timezone, UTC offsets, and display instants;
- the selected deterministic cortisol model's public name/version, units, parameters,
  samples, population/reference band, wake/sleep markers, coverage features, and
  explanatory safety/methodology text already shown by the chart;
- recorded dose display facts: medication display name, amount, unit, route, local
  time, and regular/stress/emergency classification;
- Garmin chart facts and daily summaries used by the chart: metric type, unit,
  observed time, value, cadence, sleep intervals, missingness/coverage state, and
  public provider label;
- recorded symptom display facts: symptom name, optional severity, local time, body
  area, and owner-selected tracking category;
- recorded blood-pressure and temperature display facts: local time, values, units,
  pulse/posture when displayed, and coarse measurement context when it is one of the
  chart's reviewed generic labels such as `home`;
- stress-episode display facts: local start/end time, status, and trigger category;
  and
- deterministic chart labels, totals, ranges, and accessible table text derived only
  from the preceding fields.

Every exported schema is an explicit projection. A newly added private model or API
field is excluded until this ADR is superseded or the public contract is deliberately
amended and tested.

The exporter must exclude owner/account identifiers, email, names, authentication and
integration credentials, database/provider/source IDs, correction/audit IDs, raw
Telegram content, drafts, notes and diary text, physician names/instructions,
documents, reports, labs, exact coordinates, unrestricted location text, AI analysis,
chat history, internal error detail, and unrelated facts. Public-local opaque keys may
be generated only when rendering requires stable list keys; they must not be derived
from private identifiers.

The owner understands that real published health facts are anonymous-access data and
may be copied, indexed, cached, archived, correlated, or retained after removal. This
authorization applies only to the allow-list above and may be revoked for future
publication without promising deletion of third-party copies.

### Eligibility and atomicity

For local day `D`, the earliest cutoff is local noon on `D + 1`: the end of `D` plus
twelve hours. A day is eligible only when all of these are true:

1. the current instant is at or after that cutoff in the configured IANA timezone;
2. a Garmin sync run has status `completed` or `completed_with_warnings`, uses the
   configured timezone, covers `D` in its requested inclusive date window, and
   finished at or after the cutoff;
3. the selected deterministic curve and every required projection validate against
   the versioned public schema; and
4. the full candidate bundle passes privacy and referential checks.

Warnings do not make a successful provider run incomplete by themselves; missingness
remains explicit and is never converted to zero. A running, failed, stale, differently
zoned, or non-covering sync cannot qualify a day. The exporter retains previously
published eligible days but never exposes a candidate file or manifest entry until
the complete new bundle has been generated and validated in a sibling staging
directory. Deployment transfers a complete staged tree and fails closed.

The calendar lists only manifest dates and defaults to the newest one. A requested
date not present in the manifest is not fetched or inferred.

### Deployment authentication and operation

Deployment uses a dedicated Ed25519 key whose private half remains outside Git and
the database. The server authorizes that key with OpenSSH `restrict`, `no-user-rc`,
and a forced `/usr/bin/rrsync -wo` command scoped to the exact
`/home/jellin2/jeffellin.com/healthcurve` directory. It grants no shell and no read or
delete capability. The client pins the reviewed host key and uses an exact host,
account, port, identity file, and destination.

The scheduled publisher records only privacy-safe operational facts: run time,
eligibility decisions by date, file counts, byte counts, bundle digest, deployment
result, and age of the latest successful publication. It never logs payload values.
The previous remote tree is recoverable from the host's ordinary backup/versioning
mechanism; deployment automation itself does not gain remote delete access.

## Consequences

- Anonymous visitors can interact with the real, owner-authorized curve without a
  route to the private application.
- The private Tailscale/password boundary, PostgreSQL, Redis, Ollama, Telegram, Garmin
  credentials, and all write operations remain unchanged and non-public.
- The public data is intentionally no longer confidential. Correct allow-listing,
  schema review, CSP/static headers, dependency hygiene, and atomic publication become
  release gates.
- Publication lags by at least twelve hours after a day ends and may lag longer when
  Garmin sync or validation is incomplete.
- Corrections to an already published day can replace that day's static projection
  only after another qualifying post-cutoff sync and full-bundle validation. Copies
  outside the owner's control may preserve prior values.
- A compromise of the dedicated deployment key can overwrite this one static
  directory but cannot read private data, obtain a shell, delete remote files, or
  reach the private application. The key must still be revoked and the site restored.

## Alternatives considered

**Expose the private application or API with authentication.** Rejected because it
creates an internet login and application attack surface and contradicts ADR-0007.

**Generate synthetic public data.** Safer, but rejected for this site because the
owner explicitly requires the authorized real curve data.

**Render screenshots only.** Rejected because it cannot provide calendar navigation,
interactive series controls, or the accessible data alternatives.

**Upload the database or a complete private export.** Rejected because it violates
data minimization and exposes credentials, identifiers, notes, and unrelated records.

**Let browser JavaScript call the private API.** Rejected because it would require a
public path into the private runtime and would move authorization mistakes into an
anonymous client.
