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
| Record a dose, symptom, or note | Telegram bot |
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

`/beads-add <feature request>` is the deliberate exception: it asks only the configured
local Ollama/Qwen model to generate a structured product proposal. The host bridge
searches for a duplicate before creating a Bead. The raw directive is not copied into
Beads, and an unavailable model, ambiguity, invalid output, prompt-injection text, or
sensitive content creates nothing. It never starts an agent or implementation. See
[Telegram feature-request bridge](beads-feature-bridge.md) for the privacy boundary and
recovery behavior.

Free text goes to the model: *"Took 15mg hydrocortisone at 7:08, slept badly"*. You get
a draft with **Confirm** / **Cancel**. Nothing becomes a record until you confirm it.

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
whether to include separately labelled AI analysis, and download the JSON file.

The API equivalent requires both the session's CSRF token and the current password:

```bash
CSRF=$(curl -s -X POST http://localhost:8080/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email": "you@example.com", "password": "..."}' \
  -c cookies.txt | python3 -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])')

curl -s -b cookies.txt -H "X-CSRF-Token: $CSRF" \
  -H 'content-type: application/json' \
  -X POST 'http://localhost:8080/api/v1/privacy/export' \
  -d '{"password": "...", "include_ai": false, "include_sensitive": true}' \
  > export.json
```

Sections are separated by category: `facts` (what you recorded), `plan` (physician-
approved), and `ai` (generated analysis, **excluded** unless you explicitly set
`include_ai` to `true`). Integration credentials are never exported. HealthCurve
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

HealthCurve now provides an authenticated `/analytics` page and
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

The planned selected-day overlay keeps symptoms, stress episodes, Garmin stress/HRV/
respiration/heart rate, and blood pressure in separate recorded-context lanes. These
observations do not alter the actual-dose exposure trace and are not converted into a
cortisol requirement, medication-demand multiplier, or adequacy/shortfall judgment.
Missing measurements remain visible gaps. See
[ADR-0015](adr/0015-recorded-context-not-cortisol-demand.md).

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

The authenticated `/data-quality` page collects items that need attention from AI
extraction drafts, rejected lab imports, and background jobs. Each finding links to
the relevant review or correction destination. It also shows metrics that the latest
Garmin source genuinely did not supply in a separate section. A provider-reported
absence is not stored or displayed as zero.

The page is a review queue, not a clinical completeness check. “No known data-quality
findings” only means that the implemented deterministic checks found nothing current;
it does not establish that every health event or measurement was recorded.

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
