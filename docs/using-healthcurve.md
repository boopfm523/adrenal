# Using HealthCurve today

What works right now, and how to get at your data. Written against the running stack,
not the plan — where something doesn't exist yet, this says so.

The authenticated web interface includes Today, timeline, dose, symptom and diary,
plan, episode, reports, data-quality, settings, and Help views. Help is the task-oriented
starting point for current Telegram commands, web/API entry methods, imports, and
clearly labelled planned features. The emergency page remains server-rendered so it
does not depend on JavaScript.

---

## The short version

| I want to… | How |
|---|---|
| Record a planned dose taken now | Today in the web interface |
| Record a dose | Web Doses or Telegram bot |
| Record a symptom or note | Telegram bot |
| See today against your plan | Web Today, `/today` in Telegram, or `GET /api/v1/doses/plan-comparison` |
| Review or correct a recorded dose | Web Doses; corrections preserve the prior value in revision history |
| Review symptoms, diary, and life events | Web Symptoms & diary; sensitive entries are hidden until explicitly revealed |
| Create, review, approve, retire, or compare plan versions | Web Plan; approval is an explicit human action with clinician provenance |
| Learn how to enter or import data | Web Help; examples are synthetic and distinguish immediate records from drafts |
| Load your medications | `python -m healthcurve.cli load-medications` |
| See everything recorded | `GET /api/v1/timeline` |
| Get all your data out | Settings → Export, or `POST /api/v1/privacy/export` |
| Sync Garmin Connect | Connect once locally, then review status/sync in Settings and observations in Health data; see [Garmin Connect](garmin-connect.md) |
| Import an exported Garmin FIT/CSV/ZIP | Preview then confirm through the API as a durable fallback; see [Garmin import](garmin-import.md) |
| Check encrypted backups | `python -m healthcurve.backup_status` in `backup-worker` |
| Answer a question the API can't | SQL — see [Analytics](#analytics) |
| Show someone what to do in a crisis | `http://localhost:8080/emergency` |

---

## Setting up your medications

Nothing can be recorded against a medication HealthCurve doesn't know about — the bot
deliberately refuses to guess at an unrecognised name.

### Guided web workflow

Open **Plan** in the authenticated web interface. Choose **Create first plan draft**
when there is no approved plan, or **Create new version from active plan** to start with
the current schedule. The form lets you:

- select an existing medication or add one to the owner's medication list;
- enter one or more scheduled times, amounts, units, routes, and optional conditions;
- set effective dates and copy physician-authored instructions with their author/date;
- cancel without writing, save an unapproved draft, and return later to edit it.

Saving never approves the plan and never records a dose as taken. Review the draft's
slots and instructions, expand **Approve this draft**, enter the clinician/role and
the source of approval, and acknowledge that this is a real clinician-approved plan.
The local LLM has no route that can perform this action. Once approved, the version is
immutable: make a new version for any change. **Retire this approved version** ends an
ongoing version while preserving its history. An unreferenced unapproved draft may be
permanently deleted with the separate password-and-phrase flow described below.

Dates and times use the values shown in the form. An effective-through value must be
later than effective-from, amounts must be positive, and every slot needs an explicit
unit and route. If an approval would overlap another approved version, HealthCurve
refuses it so there is never more than one plan in force for a moment.

### CLI alternative

```bash
# 1. Write a template you can fill in
docker compose run --rm api python -m healthcurve.cli init-medications-file meds.yaml

# 2. Edit meds.yaml with your real medications and schedule

# 3. Load it. This creates a *draft* regimen -- not yet in effect.
docker compose run --rm api python -m healthcurve.cli load-medications meds.yaml

# 4. The load command prints the new version ID. Record the approval source.
#    Until this succeeds, the plan is not active.
docker compose run --rm api python -m healthcurve.cli approve-regimen \
    <VERSION_ID> --by "Dr Smith" --source "clinic letter 2026-08-09"
```

The approval step is not bureaucracy: an unapproved regimen can't be the baseline that
adherence is measured against, because nothing has established it as correct.

An unwanted **unapproved draft** can be permanently deleted from its card on the
authenticated Plan page. Expand “Delete this unapproved draft,” enter the current
password, and type `DELETE DRAFT PLAN` exactly. HealthCurve refuses if the version was
approved or retired, belongs to another owner, or is referenced by a recorded dose,
saved report, or AI analysis. Its draft slots and draft instructions are removed with
it; medications, facts, approved history, reports, analyses, and unrelated drafts are
left unchanged. The structural audit entry contains only the deleted draft ID and row
counts, not medication, schedule, instruction, clinician, or source values. Encrypted
backups may retain the deleted draft until their configured expiry.

## Browsing recorded history

Timeline, Symptoms & diary, and Episodes open with the latest seven local calendar days,
including today, in the profile timezone. The visible From and Through fields are the
dates sent to the API. **Today**, **Yesterday**, and **2 days ago** set both fields to one
day and refresh immediately. Custom dates remain editable and become shareable URL
filters. **Clear filters** deliberately switches to all history; the page says when this
unbounded view is active so it is not confused with the seven-day default.

## Recording things

**Telegram** is the fastest path. Health-recording commands are deterministic and work
even when the language model is unavailable:

| Command | Example |
|---|---|
| `/dose` | `/dose 15 hydrocortisone` |
| `/symptom` | `/symptom nausea 4` |
| `/injection` | logs an emergency injection |
| `/episode` | `/episode start vomiting` / `/episode end` |
| `/today` | today's doses against your plan |
| `/undo` | cancels the pending draft |
| `/bd-list` | current bounded `bd list` output from the trusted host bridge |
| `/bd-status` | current bounded `bd status` output from the trusted host bridge |

`/bd-add <feature request>` is the deliberate model-backed exception; `/beads-add`
remains a compatibility alias. It asks only the configured local Ollama/Qwen model to
generate a structured product proposal. The host bridge
searches for a duplicate before creating a Bead. The raw directive is not copied into
Beads, and an unavailable model, ambiguity, invalid output, prompt-injection text, or
sensitive content creates nothing. It never starts an agent or implementation. See
[Telegram Beads bridge](beads-feature-bridge.md) for the privacy boundary and
recovery behavior.

Ordinary phrases such as “show the current bd list,” “what is the Beads status,” and
“add a Bead for hydration tracking” use a separate schema-constrained local-model
intent with only `list`, `status`, `add`, or `none`. The first two queue the same fixed
read operations as the slash commands; `add` still passes through the full proposal
validation. The application and model never control a shell command, argument, path,
priority, or status. If the model is unavailable, the bot guesses nothing and points
back to `/bd-list`, `/bd-status`, and `/bd-add`.

Free text goes to the model: *"Took 15mg hydrocortisone at 7:08, slept badly"*. You get
a draft with **Confirm** / **Cancel**. Nothing becomes a record until you confirm it.

The Doses page and Telegram drafts distinguish **Regular dose** from **Stress dose /
up-dose**. Regular is always the default. Select or explicitly say “stress dose” or
“up-dose” when that is what happened; illness, symptoms, stressful context, or an open
episode alone never silently changes a dose into a stress dose. An optional episode
link adds context but remains separate from the recorded dose category.

If a draft says *"you didn't give a time, so I've used when you sent this"*, that is the
time that will be recorded — the draft always shows the value that will be written.

### Weight display and history

HealthCurve accepts weight in either pounds or kilograms while preserving the exact
entered value and unit as part of the recorded fact. The Health data chart, history
table, Timeline summary, Telegram confirmation, and physician-report snapshot use
pounds as the primary presentation unit so mixed-unit history stays on one scale.

Presentation conversion is deterministic: `1 lb = 0.45359237 kg`, with displayed
pounds rounded half up to `0.1 lb`. The stored original value/unit and the normalized
kilogram value are not rewritten by this presentation conversion. The compact history
table shows the entered measurement as provenance and keeps immutable correction
history and correction controls available. The chart's adjacent data table is the
authoritative accessible alternative; missing intervals are not inferred as zero.

### Body-temperature display and history

HealthCurve accepts measured body temperature in Fahrenheit or Celsius and preserves
the entered decimal value and unit as the recorded fact. Presentation is always
Fahrenheit first with Celsius in parentheses—for example `100.4 °F (38.0 °C)`—in
Health data, Timeline, exports, reports, Telegram confirmations, and HealthCurve.

Conversion is deterministic: `°F = (°C × 9/5) + 32`, with displayed values rounded
half up to `0.1` degree. The API accepts the broad structural human-measurement range
`25–45 °C` (`77–113 °F`) to reject unit mistakes; this is not a fever classification
and HealthCurve does not diagnose or interpret the reading. Corrections create a new
fact and retain the original value, unit, time, source, and correction reason.

## The HTTP API

Everything is behind Caddy on `http://localhost:8080`, and every API route is
under **`/api/v1`**. Log in first:

```bash
curl -s -X POST http://localhost:8080/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email": "you@example.com", "password": "..."}' \
  -c cookies.txt | python3 -m json.tool
```

That sets a session cookie and returns a `csrf_token`. Reads need only the cookie
(`-b cookies.txt`); **writes additionally need the token** in an `X-CSRF-Token` header,
because a cookie alone must never be enough to cause a write (threat model T1).
Interactive docs are at `http://localhost:8080/docs` in development.

```bash
# Everything recorded, newest first
curl -s -b cookies.txt 'http://localhost:8080/api/v1/timeline?limit=50' | python3 -m json.tool

# Doses in a window. Corrected-away versions are excluded by default, so totals
# are correct; add include_superseded=true to see the history.
curl -s -b cookies.txt \
  'http://localhost:8080/api/v1/doses?date_from=2026-08-01T00:00:00' | python3 -m json.tool

# Today vs the approved plan -- on time, late, or missing
curl -s -b cookies.txt 'http://localhost:8080/api/v1/doses/plan-comparison?day=2026-08-09' \
  | python3 -m json.tool
```

Filters are `date_from` / `date_to` (ISO datetimes) on both `/doses` and `/timeline`.

`plan-comparison` is worth understanding: `missing` slots are **derived**, never stored.
A dose you didn't take doesn't create a row saying so — the comparison works out what
the plan expected and what the record contains, every time you ask.

## Getting all your data out

The easiest export is **Settings → Export**. Enter your current password, choose
whether to include sensitive diary/life-event text and separately labelled AI
analysis, then request the export. Complete exports run in the background. The page
shows durable progress, automatic retry status, a safe error code when applicable,
and a download link after completion. The link expires seven days after the request;
refreshing or leaving the page does not lose it.

The API equivalent requires both the session's CSRF token and the current password:

```bash
CSRF=$(curl -s -X POST http://localhost:8080/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email": "you@example.com", "password": "..."}' \
  -c cookies.txt | python3 -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])')

EXPORT=$(curl -s -b cookies.txt -H "X-CSRF-Token: $CSRF" \
  -H "Idempotency-Key: cli-export-$(date +%s)" \
  -H 'content-type: application/json' \
  -X POST 'http://localhost:8080/api/v1/privacy/export' \
  -d '{"password": "...", "include_ai": false, "include_sensitive": true}')

# Poll the returned status URL (or GET /api/v1/privacy/exports), then stream the
# download_url once status is "completed".
echo "$EXPORT" | python3 -m json.tool
```

Sections are separated by category: `facts` (what you recorded), `plan` (physician-
approved), and `ai` (generated analysis, **excluded** unless you explicitly set
`include_ai` to `true`), plus separately labelled integration provenance and report
metadata. Exact fact revisions, Garmin source provenance, lab facts, and retained lab
source PDFs are included. Integration credentials are never exported. HealthCurve
does not expose a cookie-only legacy export route.

## Emergency page

```
http://localhost:8080/emergency
```

Server-rendered, no JavaScript, no database writes needed to display, and it keeps
working when Ollama and Redis are down. Without a valid session it shows only generic
advice to call local emergency services and check the person's device Medical ID or
physical emergency card. It does not reveal a diagnosis, medications, contacts, or
instructions and does not show the injection form.

When you are logged in, the page additionally shows dated physician-authored
instructions and the fast injection form. HealthCurve is not the responder-facing copy
of your emergency plan: keep Medical ID or a physical card current so it still works
when your device is locked, this host is down, or Tailscale is unavailable. See
[ADR-0011](adr/0011-tiered-emergency-page-access.md).

## Encrypted backups

Nightly encrypted local and Google Drive backups, checksum verification, retention,
and privacy-safe age/dead-letter status are implemented through a dedicated worker.
The recovery identity remains separately stored in macOS Passwords and is never kept
with the backup or in HealthCurve configuration. The first newest-offsite isolated
restore drill passed on 2026-08-10, including key recovery, exact sentinel and artifact
checks, database and API smoke tests, the 24-hour RPO, the four-hour RTO, and teardown.
Follow [backup-runbook.md](backup-runbook.md) for status checks and quarterly drills.

The owner declined a separate external drive, so this is an accepted two-failure-domain
layout rather than strict 3-2-1: loss of the Mac disk removes both the live database and
local encrypted copy, while the independently encrypted Google Drive copy remains.

## Lab PDF source documents

`POST /api/v1/labs/documents` accepts an authenticated PDF upload into a private
quarantine. The API checks the declared media type, PDF signature, and 25 MiB byte cap
while streaming to an opaque generated path outside the web root. A separate non-root
`document-worker` with no network access runs pinned qpdf structural, encryption,
interactive-content, and 100-page checks. Until that completes, the document status is
`pending`; malformed, encrypted, over-limit, or interactive PDFs become `rejected` with
a stable reason code and their bytes are removed.

Accepted source PDFs can only be retrieved through an owner-authenticated,
attachment-only download with a CSP sandbox, `nosniff`, and `no-store`. HealthCurve
never embeds or renders the raw PDF in the browser. The networkless worker instead
creates bounded inert PNG page previews; the owner-authenticated preview route serves
only those PNGs with `nosniff`, `no-store`, and a deny-by-default CSP.

The Labs page exposes a deliberate permanent-deletion review for every upload. An
unconfirmed upload requires its target-specific confirmation phrase. A confirmed
report additionally requires the current password and previews exact opaque IDs and
counts for the document, extraction draft, panel/results, derived trend points, AI
analyses, saved report snapshots/artifacts, page previews, and private files. Because
saved reports are immutable, deleting a source also deletes any entire saved report
snapshot that contains it; the preview says so before authorization.

The database deletion and opaque cleanup jobs commit as one transaction. A dedicated
internal-only cleanup worker tombstones and idempotently removes source/previews and
report artifacts with bounded retries; it has no Telegram, Ollama, Redis, provider
credential, or internet access. Linked facts are never orphaned and unrelated records
are retained. Backup configuration includes `HC_UPLOADS_DIR` and report artifacts, so
deleted copies remain in encrypted backups only until the documented retention window
expires.

For local Compose, create `var/uploads` as an owner-private directory before startup,
or set `HC_UPLOADS_DIR` to another private absolute host path. Never put source medical
PDFs in Git or under the frontend/web root.

For a validated digital PDF, the same networkless worker uses pinned pdfplumber to
group explicit text by page and geometry. It only recognizes a row after finding
explicit analyte/test and value/result column headings. Parsed rows and every other
non-empty line are written as review candidates; unmatched lines are labeled
`unparsed_row`, never discarded or guessed. `GET /api/v1/labs/documents/{id}/extraction`
imports that mailbox result into the AI draft namespace and returns its page boxes,
extractor/schema versions, and `requires_confirmation: true`. This creates no lab fact
and makes no model call.

If a page has no embedded words, the worker renders only that page with pinned Poppler
and runs pinned English Tesseract OCR. Rendering is capped at 2,400 pixels per
dimension, 5.76 million pixels per page, 100 million pixels per document, 30 seconds
per command, 120 seconds per document, and 4 MiB of TSV output. OCR candidates retain
rendered-pixel boxes and word confidence; values below 0.8 and rows that cannot be
parsed remain visible as `low_confidence`/`unparsed_row`. Page PNG and TSV scratch data
live in a temporary directory and are purged on success or failure. The extraction
records whether it used `embedded_text`, `ocr`, or `mixed`; OCR still creates only a
confirmation-required draft.

For review, the networkless worker publishes a bounded inert PNG for every validated
page. The per-document renderer reduces resolution for unusually long PDFs so the
100-million-pixel ceiling still applies. If OCR still cannot produce a high-confidence
row, HealthCurve sends only that already-generated PNG
and capped lower-tier candidates through the private Ollama boundary to
`qwen3-vl:30b`. The response is constrained to a strict schema and rejected unless
every candidate cites the expected page and a bounding box inside the rendered image.
The draft records the model tag, immutable digest, prompt version, page evidence, and
`model_generated` flag; suspected prompt-injection text is flagged. Earlier OCR
evidence remains beside the proposal and is never overwritten. A missing model,
timeout, invalid schema, missing preview, or empty result becomes an explicit unparsed
candidate and creates no fact or plan. Pull the selected local model before relying on
this fallback:

```bash
ollama pull qwen3-vl:30b
```

The authenticated `/labs` page implements the confirmation boundary. Upload a PDF,
wait for local validation/extraction, then compare every candidate with the inert source
page preview shown beside it. You can correct analyte, value, unit, and range or exclude a row;
specimen time, report time, timezone, and specimen type remain explicit owner inputs.
Unparsed evidence stays visible but cannot be guessed into a fact. Confirming creates
only the included rows with `confirmed_from_draft` provenance. Each result retains the
source document ID and page number, and its Labs-table link opens that exact inert page
preview. The original PDF remains available separately as an attachment download.
Confirmation fails closed if the page preview is missing, and the document worker
retries missing previews. Unconfirmed drafts never appear in trends or physician reports.

---

## Location, timezone, and weather context

Authenticated clients can record contextual observations through
`POST /api/v1/context-events`, list current observations with
`GET /api/v1/context-events`, and request retained correction history with
`include_superseded=true`. Each observation stores the experienced local time, IANA
timezone, resolved UTC instant, and historical offset together. This preserves travel
and daylight-saving context without rewriting any health event.

HealthCurve keeps the full IANA identifier as the canonical stored and API value, but
human-facing time references show the timezone-database abbreviation in force on the
referenced date (for example, `EST` in winter and `EDT` in summer). Configuration and
filter fields remain labeled as IANA timezone inputs because abbreviations are ambiguous
and are not safe identifiers.

Location privacy is explicit. `none` stores no location; `coarse` requires a label
such as a city or region and forbids coordinates; `exact` requires a latitude,
longitude, and `exact_location_consent=true`. Those rules are enforced by both the API
and PostgreSQL. Context is a separate fact type, so password-confirmed
`DELETE /api/v1/context-events/{id}` removes its complete correction chain without
deleting doses, symptoms, diary entries, or other medical facts.

Weather values always carry an observation time, provider, and explicit units. Only
the `manual` provider is accepted today. HealthCurve makes no external weather or
geocoding request in this implementation; provider-based enrichment remains blocked
until the owner chooses a service and explicitly approves transmitting location.

The **Settings & privacy** page provides the owner workflow: coarse-by-default entry,
per-record exact-coordinate consent, optional manual weather, retained-context review,
and password-confirmed deletion. Context also appears on the Timeline as
“Environmental context,” visually and textually separate from health facts,
physician-approved plans, and AI analysis. Exact coordinates are never included in a
timeline summary.

## Analytics

HealthCurve now provides an authenticated `/healthcurve` page (`/analytics` redirects
there for compatibility) and the
`/api/v1/analytics/summary` endpoint. They compute deterministic daily plan-versus-
actual totals, dose timing, stress-episode duration, symptom frequency/severity, and
missingness for a selected local-date range and IANA timezone. Every result states its
definition, timezone, sample count, and missing values. There are no inferential
insights or causal claims. Daily plan-versus-actual totals also have a local SVG chart
whose adjacent semantic table is authoritative and preserves gaps. The report API
freezes those deterministic values for reproducible physician reports.

`GET /api/v1/analytics/steroid-exposure?day=YYYY-MM-DD&timezone=Area%2FCity`
returns the selected local day's versioned theoretical hydrocortisone-exposure series
from current actual dose facts, including the preceding 24-hour carryover window. It
keeps each administration marker separate from its modeled peak and pointwise-sums
distinct doses even when they were recorded close together or at the same instant.
Version 1 supports only conventional immediate-release oral hydrocortisone tablets in
mg; unsupported medication/formulation/route/unit combinations remain visible as dose
markers with a reason and do not silently borrow parameters. The series is in relative
exposure units (REU), not serum cortisol, biological effect, clinical coverage, or
dosing advice. See [ADR-0013](adr/0013-theoretical-steroid-exposure-model.md).

The selected-day HealthCurve overlays the actual-dose exposure shape, Garmin stress/
HRV/respiration/heart rate, blood pressure, body temperature, discrete symptom markers, dose markers,
stress-episode windows, and Garmin sleep sessions on one local-time graph. Sleep start
and wake/end use labeled vertical markers; explicitly timed awake intervals use a
distinct amber background. Overnight sessions appear on every selected local day they
overlap. A reported awakening count without interval timestamps is labeled unavailable
rather than distributed or inferred across the night. The selected-day data refreshes
periodically so a completed Garmin sync appears without manual sleep entry. Hovering
over the graph or using
its keyboard/mobile time control shows exact nearby observations with their original
units and timestamps. Continuous shapes use a clearly labeled relative 0–100 display
position so unlike units can be compared in time without implying that their values
are equivalent. The initial focused view compares theoretical exposure with Garmin
stress; one-click controls switch to heart rate, HRV, respiration, blood pressure,
temperature,
recorded events, or an intentionally busy all-series view. Dense wearable sample dots
are hidden from the graph while every exact value remains available through hover and
the table. Lines connect only samples with an observed contiguous cadence;
unknown and interrupted intervals remain gaps. Garmin daily or nightly summaries are
shown as untimed aggregate context and in the exact-value table; HealthCurve does not
place them on the intraday axis, stretch an average across hours, or invent a curve.
The exact-value/provenance table is authoritative.

Garmin aggregate context can include nightly-average HRV, waking- and sleeping-period
average respiration, and daily respiration low/high. Garmin's current client does not
provide a distinct all-day HRV average, so HealthCurve reports it as unsupported and
does not calculate or relabel another value as a substitute. Missing aggregate fields
stay missing rather than appearing as zero.

These observations do not alter the actual-dose exposure trace and are not converted
into a cortisol requirement, medication-demand multiplier, or adequacy/shortfall
judgment. See
[ADR-0015](adr/0015-recorded-context-not-cortisol-demand.md).

### Exact HealthCurve formulas and evidence

The Analytics page publishes the executable `hc-exposure-v1` formula, its live
parameter values, model version, evidence links, and limitations under **How this model
works: formulas, sources, and limits**. For elapsed hours `t` after a supported actual
dose, the implementation is exactly:

```text
ka = 2 per hour
ke = ln(2) / 1.7 hours
t_peak = ln(ka / ke) / (ka - ke)
raw(t) = exp(-ke*t) - exp(-ka*t)
shape(t) = raw(t) / raw(t_peak)
dose_contribution(t) = recorded_amount_mg * shape(t) REU
total_exposure(t) = sum of every supported current dose contribution
```

Each response sample also exposes `regular_exposure_reu` and `stress_exposure_reu`.
Only doses explicitly categorized as stress contribute to the stress component;
scheduled, late, replacement, taper, and emergency categories are grouped into the
regular component. The two components sum to the plotted total and no supported dose
is counted twice. The hover tooltip shows present components to three decimals and
actual dose markers identify their recorded category.

Contributions are zero before administration and after 24 elapsed hours. They are
sampled every five elapsed minutes plus exact administration and modeled-peak knots.
The peak normalization of 1 REU per recorded mg is a HealthCurve visualization choice,
not a claim that serum cortisol is dose-proportional. The one-compartment shape and
parameters are explained and sourced to [Derendorf et al.](https://doi.org/10.1002/j.1552-4604.1991.tb01906.x),
[Johnson et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC5963674/),
[Werumeus Buning et al.](https://doi.org/10.1016/j.metabol.2017.02.005), the
[Endocrine Society guideline](https://pmc.ncbi.nlm.nih.gov/articles/PMC4880116/), and
[Röhr et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC9231005/). Full rationale,
gold cases, unsupported formulations/routes, and uncertainty are in
[ADR-0013](adr/0013-theoretical-steroid-exposure-model.md).

For display only, each non-stress, non-symptom numeric lane uses
`display = 100 * (value - display_min) / max(display_max - display_min, 1)`, where
the bounds are the observed selected-day minimum and maximum. An empty lane uses 0 and
1. If every point equals `v`, fallback bounds are `min(0, v)` and
`v + max(1, abs(v) * 0.1)`. Garmin stress uses fixed 0–100 bounds. A symptom retains
its recorded 0–10 severity and is positioned at `severity * 10`. Exact native values
stay in the readout and table.

HealthCurve currently has **no baseline, Garmin-stress-derived, or symptom-derived
cortisol “needed” formula**. The supplied exploratory model proposed
`Req(t) = Base(t) * S(t)`, but its baseline anchors and stress multipliers are not used
by `hc-exposure-v1`. [Boonen et al.](https://pubmed.ncbi.nlm.nih.gov/23506003/),
[Prete et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC7241266/), and
[Lewis and Elder](https://pmc.ncbi.nlm.nih.gov/articles/PMC3813945/) show why critical
illness, administration method, binding, and free-versus-total cortisol complicate a
physiological requirement. They do not validate converting a Garmin stress score or
subjective symptom severity into an individual minute-by-minute cortisol requirement.
Any future experimental demand line therefore requires a new versioned model and ADR,
explicit uncertainty, validation data, and language that cannot be read as dosing
advice. See [ADR-0015](adr/0015-recorded-context-not-cortisol-demand.md).

`GET /api/v1/analytics/daily-patterns?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&timezone=Area%2FCity`
derives up to 366 comparable local-day rows from current facts. Each row states
`hc-daily-pattern-v1`, its exposure-model version, actual dose-linked plan-version
IDs, a correction/provider-revision-sensitive source fingerprint, exposure peak and
REU-hours, symptom timing relative to the latest supported dose and theoretical
exposure, native-unit Garmin ranges with cadence-derived observed coverage, discrete
blood-pressure ranges, and stress-episode overlap. Missing samples and unavailable
cadence stay explicit; expected Garmin samples are never invented. The endpoint
recomputes rather than storing derived features, so a fact correction or provider
revision changes the values and fingerprint without rewriting a recorded fact.

The Analytics page renders these rows in an exact-value comparison table beneath the
selected-day HealthCurve. The same bounded projection is downloadable from
`GET /api/v1/analytics/daily-patterns.csv`; the CSV is derived content and contains no
credentials or AI interpretation.

The longer-range section also summarizes one deterministic value per local day. It
shows minimum, median, maximum, observed-day coverage, missing-day count, and a
first-to-last change for theoretical exposure AUC, recorded symptom severity, Garmin
stress, heart rate, HRV, and respiration. The change is withheld until at least seven
days contain that metric. This is a display guard, not a statistical-significance
claim. “Observed coverage” means data availability only—not cortisol sufficiency,
physiological coverage, or whether a medication plan met the body's needs. Contiguous
feature/exposure model-version periods remain visible, and each row links back to its
day-level HealthCurve. Reviewing many measures can surface chance patterns, so the
section explicitly warns that descriptive correlation or association does not
establish causation or diagnosis.

The optional private Ollama pattern explanation can phrase only those deterministic
range figures. The browser shows elapsed time, stops waiting after 75 seconds, and
offers an explicit **Stop waiting** control. Stopping or timing out ends only the
browser's wait: host Ollama may still finish, so **Check for a completed draft** or a
page refresh reloads the latest saved completion for the exact range and timezone.
Failed and unfinished requests are not stored. A saved draft stays in the AI namespace
and carries its daily feature IDs and dates, model digest, prompt version, schema
version, missingness, and uncertainty caution. HealthCurve rejects and does not save
output that invents a number, omits required citations, or gives medication guidance.
If configured host Ollama or its model is unavailable, deterministic results continue
to work and the UI provides a safe retry. The owner can delete the generated draft
without changing facts or physician-approved plans; while retained, it is included
only when an export explicitly includes AI analysis. Health text goes only to the
configured private Ollama adapter, never to a cloud AI service.

For one selected day, **Analyze this day** builds a fresh fingerprinted projection from
all supported recorded domains and the physician-approved plan active that day. Dense
Garmin and theoretical-exposure readings are summarized into fixed 15-minute local-time
windows so every sample contributes without overwhelming the private model. Sparse
records retain their exact times and values; absent domains remain explicitly missing.
The projection may include sensitive diary and life-event text, which is stated beside the button,
but exact coordinates are withheld. It is sent only to the configured host-native
Ollama model—never a cloud AI service.

The saved result is visibly AI-generated and retains its selected date/timezone, source
revision fingerprint, source-record manifest, model digest, prompt version, and schema
version. The full day projection is sent transiently to local Ollama but is not duplicated
in the saved provenance row. HealthCurve
rejects output that lacks citations, invents numeric values, omits missingness, or gives
medication guidance. A timeout or missing/malformed model response leaves the daily
HealthCurve usable and saves nothing. If a later Garmin sync or fact correction changes
the projection, the saved interpretation is marked stale until you analyze the day
again. The analysis may suggest descriptive associations or questions to review; it
does not establish causation or diagnosis, measure cortisol, determine medication need,
or alter any recorded fact or physician-approved plan.

Historical dose timing is resolved against the physician-approved plan whose half-open
effective interval contained each scheduled or recorded instant, including retired
plans and a plan transition within a day. The timing result shows signed minutes
(actual minus scheduled), absolute minutes for each matched dose, total and average
absolute deviation, and a breakdown by historical plan period. Missing schedule slots
and unplanned doses retain their own counts and are never treated as zero-minute
deviations. Correcting a dose's occurrence time recalculates its applicable plan and
slot association without changing the preserved original fact.

### Physician-report API

Authenticated clients can create an immutable physician-report snapshot with
`POST /api/v1/reports`. A request selects an inclusive local-date range, IANA
timezone, and sections such as deterministic metrics, doses, approved plan, episodes,
symptoms, injections, patient notes, life events, labs, and wearables. The maximum
range is 366 days. Sensitive notes are excluded unless explicitly requested. AI is
off by default and requires a separate opt-in.

Every snapshot freezes its source-record manifest, exact metric values and
definitions, category-separated content, render version, and checksum. Later source
corrections or deletion do not rewrite the historical snapshot. Playwright renders
the PDF with a local network-blocked Chromium process; report content is not sent to
an external service. PDF is always generated, with CSV and JSON companions on
request. Files live under the private `HC_REPORT_ARTIFACTS_DIR` (default
`./var/reports`), outside the Caddy web root, and are included in configured backups.

`GET /api/v1/reports` lists snapshots, `GET /api/v1/reports/{id}` returns the frozen
preview data, and `GET /api/v1/reports/{id}/artifacts/{pdf|csv|json}` verifies the
artifact checksum before a no-store download. Generation and downloads produce only
structural audit metadata—never report contents.

The authenticated `/reports` page provides the same workflow without API calls:
choose the range and sections, optionally request CSV/JSON, generate locally, preview
the four category boundaries, and download any retained artifact from snapshot
history. AI and sensitive notes are both off by default and display an explicit
warning when selected.

## Data quality

An open stress episode is valid and may span days. After it has remained open for
24 hours, HealthCurve adds a review item to Data quality with its recorded start
time and elapsed duration. The item links to the normal episode workflow, where you
can leave it open or enter its actual end time. HealthCurve never infers or writes an
episode end time from elapsed duration.

The authenticated `/data-quality` page collects items that need attention from AI
extraction drafts, rejected lab imports, and background jobs. Each finding links to
the relevant review or correction destination. It also shows metrics that the latest
Garmin source genuinely did not supply in a separate section. A provider-reported
absence is not stored or displayed as zero.

The page is a review queue, not a clinical completeness check. “No known data-quality
findings” only means that the implemented deterministic checks found nothing current;
it does not establish that every health event or measurement was recorded.

A Garmin warning row represents the latest completed sync run, not another queued
request. Warnings from that run are grouped together with its covered date window and
completion time. “Clear reviewed notice” records that the owner reviewed that sync and
hides the notice without deleting imported facts or sync provenance. A later completed
sync with warnings is a new review item and appears again.

What you have today for answering questions:

1. **`/analytics/steroid-exposure`** — theoretical actual-dose exposure for one local day.
2. **`/analytics/summary`** — deterministic metrics for an inclusive date range.
3. **`/doses/plan-comparison`** — plan comparison for a single day.
4. **`/timeline`** — everything, newest first, paged.
5. **`/exports`** — the whole record as JSON, for analysis in whatever you like.
6. **SQL** — the honest answer for anything else.

### Querying directly

```bash
docker compose exec postgres psql -U healthcurve -d healthcurve
```

The schema is partitioned deliberately: `fact` is what you recorded, `plan` is
physician-approved, `ai` is generated, `ops` is operational, `identity` is accounts.
That split is what makes "show me only things I actually recorded" a trivial query.

**Doses per day over the last fortnight:**

```sql
SELECT local_time::date AS day, count(*), sum(amount) AS total_mg
FROM fact.dose_event
WHERE local_time >= now() - interval '14 days'
GROUP BY day ORDER BY day;
```

**Time-of-day distribution — are you consistent?**

```sql
SELECT date_trunc('hour', local_time)::time AS hour, count(*)
FROM fact.dose_event
GROUP BY hour ORDER BY hour;
```

**Symptoms against dose timing on the same day:**

```sql
SELECT s.local_time, s.name, s.severity,
       (SELECT max(d.local_time) FROM fact.dose_event d
        WHERE d.local_time <= s.local_time
          AND d.local_time::date = s.local_time::date) AS previous_dose
FROM fact.symptom_event s
ORDER BY s.local_time DESC LIMIT 50;
```

**Always query `local_time` for anything about a person's day**, and `occurred_at` for
anything about elapsed time. `local_time` is the wall clock you experienced;
`occurred_at` is the UTC instant. Across a DST change or a flight they disagree, and
that disagreement is the point — see [ADR-0001](adr/0001-postgresql-datastore.md).

### Corrections, not edits

A correction never overwrites. It writes a new row that supersedes the old one, so the
original stays visible (`SAFE-08`). To see only current values:

```sql
SELECT * FROM fact.dose_event d
WHERE NOT EXISTS (SELECT 1 FROM fact.dose_event s WHERE s.supersedes_id = d.id);
```

Anything without that filter is showing you history as well as the present.

### Clearing test data

Use the Plan page for one unapproved, unreferenced draft. Do not use raw SQL to remove
an approved or retired plan version: those versions are retained so historical doses
and reports stay interpretable.

The one exception is the exact legacy sample emitted by HealthCurve's original
medication template. In a **development/build-mode installation only**, preview it:

```bash
docker compose run --rm api \
  python -m healthcurve.cli purge-synthetic-medication-bootstrap
```

The command is not a search by words such as “Example” or “synthetic.” It requires a
versioned whole-record fingerprint, prints every targeted opaque ID and per-table row
count, and refuses modified, ambiguous, referenced, or non-development data. Preview
mode never changes the database. If—and only if—the IDs are the sample rows you intend
to remove, rerun interactively:

```bash
docker compose run --rm api \
  python -m healthcurve.cli purge-synthetic-medication-bootstrap --execute
```

Type the preview-bound phrase exactly. Do not paste it into shell arguments or an
automation. HealthCurve rechecks the complete fingerprint and all dose, injection,
other-plan, report, AI-draft/analysis, and source-document references in the same
transaction. It removes the exact legacy regimen, slots, placeholder instructions,
and its three now-unreferenced sample medications; unrelated rows remain. Encrypted
backups may retain deleted sample rows until their configured expiry. The command will
not remove test facts that reference the sample plan—delete those through their safe
record-specific workflow first.

The following emergency testing shortcut is intentionally unsafe and is not the normal
deletion workflow:

```sql
TRUNCATE fact.dose_event, fact.symptom_event, fact.diary_event, fact.life_event,
         fact.emergency_injection_event, fact.stress_episode,
         ai.extraction_draft CASCADE;
```

This deletes real data with no confirmation and no undo. Check which database you are
connected to first; never use it against an installation containing real records.

---

## What doesn't exist yet

So you don't go looking for it:

- **Backups are configured and recovery is proven.** Encrypted local and Google Drive
  copies run through the dedicated worker, and the first isolated restore drill passed.
  Follow [backup-runbook.md](backup-runbook.md); the next quarterly drill is tracked in
  Beads. This owner-approved layout is not strict 3-2-1 because there is no external
  local drive.
- **No official Garmin Health API or automatic weather enrichment.** Isolated
  read-only personal Garmin Connect sync, reviewed Garmin FIT/CSV/ZIP import, and
  manual context recording are implemented. Official Garmin Developer API access
  remains gated by vendor approval, and external weather requires an owner-approved
  provider and location-sharing decision.
- **Deletion is available from Settings & privacy.** Eligible individual records,
  integration data, and the complete account use password-confirmed physical deletion;
  correction-linked facts require account deletion so history cannot be made partial.
  Structural audit entries survive, and encrypted backup copies retain deleted data
  until their configured expiry.
- **Production access still needs its deployment gates.** Persistent rate limiting,
  password sessions, and integration-token encryption are implemented. HealthCurve
  intentionally has no MFA/passkey option because access is owner-only through
  Tailscale; any public exposure is prohibited until a new authentication/security
  review. Tailscale access and the isolated restore path have been deployment-verified;
  the remaining release checklist is tracked in Beads.

`bd ready` lists what's actually next.
