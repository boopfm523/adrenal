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
working when Ollama and Redis are down.

**It currently requires you to be logged in**, which is worth thinking about before you
rely on it. That protects the contents, but it also means a paramedic holding your
locked phone cannot open it, and nor can you if you are too unwell to type a password —
which is the situation it exists for. Tracked as `hc-h0e`; the resolution is a product
decision, not a bug fix.

## Encrypted backups

Nightly encrypted local backups, checksum verification, retention, and privacy-safe
age/dead-letter status are implemented through a dedicated worker. Setup is deliberately
not automatic because you must choose a separate backup medium and create recovery
material away from this host. Follow [backup-runbook.md](backup-runbook.md) before
putting data you care about into HealthCurve.

The concrete offsite provider and first isolated restore drill are still outstanding.
Until both are complete, the backup system does not satisfy the production three-copy
or proven-recovery requirements.

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
- **No lab results.** PDF upload and lab storage are designed but not built.
- **No Garmin or weather.** Not started.
- **No record/account deletion.** Backup retention exists; application-data deletion
  and account closure are tracked separately.
- **No rate limiting or MFA.** Integration token encryption and rotation are
  implemented; provider connections beyond Telegram are not.

`bd ready` lists what's actually next.
