# Using HealthCurve today

What works right now, and how to get at your data. Written against the running stack,
not the plan — where something doesn't exist yet, this says so.

**There is no web interface yet.** Today there are three ways in: the Telegram bot, the
HTTP API, and the command line. The emergency page is the one exception — it's a
server-rendered page you can open in a browser.

---

## The short version

| I want to… | How |
|---|---|
| Record a dose, symptom, or note | Telegram bot |
| See today against your plan | `/today` in Telegram, or `GET /api/v1/doses/plan-comparison` |
| Load your medications | `python -m healthcurve.cli load-medications` |
| See everything recorded | `GET /api/v1/timeline` |
| Get all your data out | `POST /api/v1/exports` |
| Import an exported Garmin FIT/CSV/ZIP | Preview then confirm through the API; see [Garmin import](garmin-import.md) |
| Check encrypted backups | `python -m healthcurve.backup_status` in `backup-worker` |
| Answer a question the API can't | SQL — see [Analytics](#analytics) |
| Show someone what to do in a crisis | `http://localhost:8080/emergency` |

---

## Setting up your medications

Nothing can be recorded against a medication HealthCurve doesn't know about — the bot
deliberately refuses to guess at an unrecognised name.

```bash
# 1. Write a template you can fill in
docker compose run --rm api python -m healthcurve.cli init-medications-file meds.yaml

# 2. Edit meds.yaml with your real medications and schedule

# 3. Load it. This creates a *draft* regimen -- not yet in effect.
docker compose run --rm api python -m healthcurve.cli load-medications meds.yaml

# 4. Record that a physician approved it. Until this, the plan is not active.
docker compose run --rm api python -m healthcurve.cli approve-regimen \
    --approved-by "Dr Smith" --approved-on 2026-08-09
```

The approval step is not bureaucracy: an unapproved regimen can't be the baseline that
adherence is measured against, because nothing has established it as correct.

## Recording things

**Telegram** is the fastest path. Commands are deterministic and work even when the
language model is unavailable:

| Command | Example |
|---|---|
| `/dose` | `/dose 15 hydrocortisone` |
| `/symptom` | `/symptom nausea 4` |
| `/injection` | logs an emergency injection |
| `/episode` | `/episode start vomiting` / `/episode end` |
| `/today` | today's doses against your plan |
| `/undo` | cancels the pending draft |

Free text goes to the model: *"Took 15mg hydrocortisone at 7:08, slept badly"*. You get
a draft with **Confirm** / **Cancel**. Nothing becomes a record until you confirm it.

If a draft says *"you didn't give a time, so I've used when you sent this"*, that is the
time that will be recorded — the draft always shows the value that will be written.

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

An export is a `POST`, so it needs the CSRF token from login:

```bash
CSRF=$(curl -s -X POST http://localhost:8080/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email": "you@example.com", "password": "..."}' \
  -c cookies.txt | python3 -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])')

curl -s -b cookies.txt -H "X-CSRF-Token: $CSRF" \
  -X POST 'http://localhost:8080/api/v1/exports' > export.json
```

Sections are separated by category: `facts` (what you recorded), `plan` (physician-
approved), and `ai` (generated analysis, **excluded** unless you pass
`?include_ai=true`). Integration credentials are never exported.

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

Nightly encrypted local backups, checksum verification, retention, and privacy-safe
age/dead-letter status are implemented through a dedicated worker. Setup is deliberately
not automatic because you must choose a separate backup medium and create recovery
material away from this host. Follow [backup-runbook.md](backup-runbook.md) before
putting data you care about into HealthCurve.

The concrete offsite provider and first isolated restore drill are still outstanding.
Until both are complete, the backup system does not satisfy the production three-copy
or proven-recovery requirements.

## Lab PDF source documents

`POST /api/v1/labs/documents` accepts an authenticated PDF upload into a private
quarantine. The API checks the declared media type, PDF signature, and 25 MiB byte cap
while streaming to an opaque generated path outside the web root. A separate non-root
`document-worker` with no network access runs pinned qpdf structural, encryption,
interactive-content, and 100-page checks. Until that completes, the document status is
`pending`; malformed, encrypted, over-limit, or interactive PDFs become `rejected` with
a stable reason code and their bytes are removed.

Accepted sources can only be retrieved through the owner-authenticated explicit
download endpoint, which always uses `Content-Disposition: attachment`, CSP sandbox,
`nosniff`, and `no-store`. `DELETE /api/v1/labs/documents/{id}` tombstones the opaque ID
before removing source and validation artifacts so an in-flight worker cannot recreate
them. Backup configuration already includes the same `HC_UPLOADS_DIR`; deleted copies
remain in encrypted backups only until the documented backup retention window expires.

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

---

## Analytics

**Be clear about what exists.** `src/healthcurve/analytics/` and
`src/healthcurve/reports/` are empty packages. There is no trend analysis, no charts,
no weekly report, no adherence-over-time. Building it is phase P3 in the roadmap.

What you have today for answering questions:

1. **`/doses/plan-comparison`** — adherence for a single day.
2. **`/timeline`** — everything, newest first, paged.
3. **`/exports`** — the whole record as JSON, for analysis in whatever you like.
4. **SQL** — the honest answer for anything else.

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

There is no deletion feature yet (`hc-cbs.10`). To wipe everything you've recorded
while testing:

```sql
TRUNCATE fact.dose_event, fact.symptom_event, fact.diary_event, fact.life_event,
         fact.emergency_injection_event, fact.stress_episode,
         ai.extraction_draft CASCADE;
```

This deletes real data with no confirmation and no undo. Check which database you are
connected to first.

---

## What doesn't exist yet

So you don't go looking for it:

- **No web interface.** Not started. See [web-frontend-guide.md](web-frontend-guide.md).
- **No analytics or reports.** Empty packages.
- **No configured offsite backup or passing restore drill.** Encrypted local backup is
  implemented; follow [backup-runbook.md](backup-runbook.md). Production recovery is
  not proven until an offsite provider and `hc-cbs.2` restore drill are complete.
- **No vision fallback or PDF review UI yet.** Manual/CSV lab facts, private PDF source
  storage, deterministic embedded-text drafts, and bounded scanned-page OCR exist.
  Vision fallback and per-result review remain `hc-xo6.5.3` and `hc-xo6.6`.
- **No automatic Garmin sync or weather.** Reviewed Garmin FIT/CSV/ZIP import is
  implemented; direct Garmin API access remains gated by vendor approval.
- **No record/account deletion.** Backup retention exists; application-data deletion
  and account closure are tracked separately.
- **No rate limiting or MFA.** Integration token encryption and rotation are
  implemented; provider connections beyond Telegram are not.

`bd ready` lists what's actually next.
