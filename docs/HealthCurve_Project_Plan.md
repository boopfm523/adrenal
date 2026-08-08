# HealthCurve: Detailed Project Plan

## Instructions to the Building Agent

> **Beads is mandatory for this build.** Before implementing HealthCurve, initialize Beads in the repository and convert this roadmap into Beads epics, issues, and dependencies. After initialization, do not begin untracked implementation work. Pull the next ready task from Beads, mark it in progress, record discoveries and blockers there, and update or close it only after its acceptance criteria and tests pass. If new work is discovered, create and link a Beads issue instead of handling it invisibly.

This document defines product intent and architecture. Beads is the operational source of truth for build status.

### Beads CLI availability

At the time this plan was written, neither `bd` nor `beads` was installed or available on `PATH`, so exact syntax could not be verified. Commands in this document are **illustrative only**. The building agent must first install or locate the intended Beads implementation, run its local `--version` and `--help`, and replace the examples with commands supported by that version. Do not invent flags or place unverified commands in automation.

## 1. Vision

HealthCurve is a private personal health record and analysis application focused initially on living without adrenal glands and managing adrenal insufficiency. It should make daily recording almost effortless, preserve an accurate history of medication and health events, reveal useful trends, and generate concise physician-ready reports.

HealthCurve is not a diagnostic product, emergency service, or autonomous medication adviser. The first priority is a trustworthy longitudinal record. Analytics and AI are derived views, never substitutes for medical facts or a physician-approved plan.

### Goals

- Log medication, symptoms, diary notes, and life events from Telegram,web,csv or excel upload.
- Compare an explicitly versioned medication regimen with doses actually taken.
- Track stress/up-dose episodes and emergency injections.
- Combine subjective events with Garmin, sleep, activity, heart data, labs, location, timezone, and weather.
- Explore timelines and trends without presenting correlation as causation.
- Create legible reports for medical appointments.
- Keep data private, auditable, portable, backed up, and recoverable.
- Build incrementally, with every implementation step tracked in Beads.

### Initial non-goals

- Replacing a clinician, emergency instructions, or an official medical record.
- Automatically changing a medication plan.
- Predicting adrenal crises or diagnoses without validated clinical evidence.
- Multi-patient, insurer, billing, or commercial clinical workflows.
- Native mobile apps before the web and Telegram workflows are dependable.

## 2. Non-negotiable data and safety boundaries

HealthCurve must always separate these three categories in storage, APIs, UI, exports, and reports:

1. **Recorded facts** — what the user reported, entered, or imported: doses, symptoms, events, measurements, labs, and Garmin data. Corrections preserve history rather than silently rewriting it.
2. **Physician-approved plan** — versioned medication schedules and instructions with effective dates, approval/provenance, and optional source-document metadata. AI cannot grant approved status.
3. **AI analysis** — drafts, summaries, pattern observations, or explanations generated from facts and plans. It must be labeled, cite its source records, retain model/prompt versions, and remain deletable/regenerable without altering either facts or plans.

Additional safety rules:

- Ambiguous high-impact fields—medication, amount, unit, route, time, stress dose, and injection—require user confirmation before persistence.
- AI may compare facts with the applicable approved plan but may not prescribe, invent a dose change, or silently turn analysis into a plan.
- Emergency instructions must be physician-authored, prominently dated, available without Ollama, and paired with advice to use local emergency services when appropriate.
- The UI must never imply that missing wearable data is zero or that an exploratory association proves causation.
- Every event needs source, event time, capture time, timezone, confidence/confirmation state, and correction provenance.

## 3. Core workflows

### Natural-language capture

The user sends Telegram text such as: “Took 15 mg hydrocortisone at 7:08, slept badly, mild nausea.” HealthCurve acknowledges receipt, asks the local LLM for schema-constrained candidate events, applies deterministic validation, and replies with a compact confirmation/edit/cancel view. Nothing becomes a recorded fact until confirmed when required.

Deterministic bot commands such as `/dose`, `/symptom`, `/episode`, `/injection`, `/today`, `/undo`, and `/privacy` provide a safe fallback when the model is unavailable.

### Plan versus actual medication

A medication regimen has immutable versions and dose slots. An actual dose is a separate fact linked, when appropriate, to a scheduled slot and the plan version active at the time. Actual doses can be categorized as scheduled, late, replacement, stress/up-dose, taper, or emergency. Missed doses are annotations/events, not fabricated zero-dose records.

### Stress/up-dose episode

An episode groups a trigger, start/end, symptoms, illness/temperature, actual doses, life events, recovery notes, and outcome. It can remain open across multiple days. It must not overwrite the plan or imply that the episode dosing was physician-approved unless the applicable plan says so.

### Review and reporting

The web application provides Today, a unified timeline, plan history, actual-versus-plan views, episode review, charts, lab trends, data-quality checks, and a report builder. Reports default to recorded facts plus the approved plan; AI observations are opt-in and separately boxed.

## 4. Architecture

Start as a modular monolith: one API/web application, one worker, one database, and a private Ollama service.

```text
Telegram ─────┐
Web browser ──┼──> HealthCurve API ──> PostgreSQL
Garmin ───────┤       │                    │
Weather ──────┘       ├──> background jobs│
                      └──> local Ollama/Qwen
```

Suggested domain modules:

- `identity`: owner account, sessions, MFA/passkeys, authorization.
- `events`: canonical timeline, corrections, provenance.
- `medications`: medications, plan versions, dose slots, approved instructions.
- `episodes`: stress/up-dose and emergency injection workflows.
- `integrations`: Telegram, Garmin, location/timezone, weather.
- `labs`: panels, analytes, ranges, source metadata.
- `ai`: extraction drafts, model/prompt registry, analysis and safety gate.
- `analytics`: deterministic metrics and aggregations.
- `reports`: snapshots, rendering, PDF/CSV/JSON exports.
- `operations`: jobs, import batches, audit, backup/restore status.

## 5. Suggested technology stack

- Backend: Python 3.12/3.13, FastAPI, Pydantic, SQLAlchemy 2, Alembic. Python 3.14 may be used only after dependency compatibility is demonstrated.
- Database: PostgreSQL 16+. SQLite is acceptable only for disposable prototypes and unit tests.
- Jobs: Dramatiq or RQ with Redis; a database-backed queue is acceptable initially if kept behind an interface.
- Frontend: React, TypeScript, Vite, TanStack Query, and ECharts or Plotly. Jinja/HTMX is a valid simplification if chosen in an architecture decision record.
- AI: Ollama with Qwen3-Coder, private network only, strict JSON Schema output, timeout/retry/circuit-breaker behavior.
- Reports: HTML templates rendered to PDF using Playwright/Chromium, with CSV/JSON exports.
- Deployment: Docker Compose, Caddy TLS reverse proxy, private PostgreSQL/Redis/Ollama, personal-domain DNS.
- Quality: pytest, Hypothesis, Testcontainers, Playwright, Ruff, Pyright, pre-commit, TypeScript checks, dependency and secret scanning.

Pin dependencies and verify runtime versions in CI.

## 6. Database and event model

Use relational columns for invariants and JSON only for source payloads or truly variable metadata. Store UTC time for comparison plus the original local time, IANA timezone, and UTC offset so daylight-saving changes and travel remain interpretable.

### Shared event fields

- UUID, event type, UTC occurrence time, original local time, timezone, offset.
- Recorded time, source type, provider/source ID, import batch.
- Confirmation state and field-level certainty where applicable.
- Provenance, notes, owner, and source revision/checksum.
- Correction/supersession link; retain original history.

### Main entities

- `UserProfile`: locale, default timezone, privacy preferences.
- `Medication`: normalized name, formulation, strength, dose unit, active dates.
- `RegimenVersion`: effective interval, draft/physician-approved/retired status, clinician/source, approved date, source-document checksum.
- `RegimenDoseSlot`: medication, local scheduled time or rule, amount, conditions.
- `ApprovedInstruction`: plan version, illness/procedure/exercise/emergency category, physician-authored text.
- `DoseEvent`: medication, decimal amount and unit, route, actual time, schedule relation, reason/category, referenced plan version.
- `StressEpisode`: start/end/status, triggers, severity, illness data, linked events, recovery.
- `EmergencyInjectionEvent`: medication, amount/unit, route/site, reason, injector, response, transport/contact metadata.
- `SymptomEvent`: name/code, defined severity scale, duration, body area, notes.
- `DiaryEvent` and `LifeEvent`: text, tags, category, sensitivity, interval.
- `SleepSession`: start/end, score, stages when available, source/revision.
- `BiometricSample` or `BiometricSummary`: heart rate, HRV, stress, body battery, value/unit, interval, aggregation.
- `ActivityEvent`: type, interval, duration, intensity minutes, distance/energy when available.
- `LocationContext`: coarse location by default, optional coordinates, timezone, confidence, retention class.
- `WeatherContext`: time/location, provider, temperature, pressure, humidity, precipitation, conditions.
- `LabPanel` and `LabResult`: specimen/report time, analyte, value or qualitative result, original unit, original reference range, abnormal flag, source.
- `ExtractionDraft`: raw-input reference, candidates, confidence, prompt/model/schema versions, user corrections, state.
- `AIAnalysis`: type, source record IDs/date range, result, model/prompt version, generated time, disclaimer.
- `ImportBatch`: integration, cursor/window, status, counts, checksum/errors.
- `ReportSnapshot`: selected range/sections, included record IDs, metric values, render version, checksum.
- `AuditEntry`: actor, action, target, timestamp, correlation ID, change reference/diff.

### Data invariants

- Medication amounts use decimal numeric types plus explicit units, never binary floats.
- Plan versions remain historically queryable and do not overlap unintentionally.
- Actual doses never become plan records and vice versa.
- Facts, plans, and AI outputs occupy separate tables/namespaces.
- Provider imports are idempotent by provider ID and revision/checksum.
- Analytics and reports state their timezone and metric definition.

## 7. API design

Use an authenticated, owner-scoped, versioned JSON API such as `/api/v1`, with generated OpenAPI documentation, stable cursor pagination, and idempotency keys for mutations.

Representative endpoints:

- `POST /events/drafts`, `POST /events/drafts/{id}/confirm`, `POST /events/{id}/correct`
- `GET /timeline?from=&to=&types=&timezone=`
- `POST/GET /doses`, `GET /doses/plan-comparison`
- `POST/GET /regimens`, `POST /regimens/{id}/approve`, `POST /regimens/{id}/retire`
- `POST/GET/PATCH /stress-episodes`, `POST /emergency-injections`
- `POST/GET /symptoms`, `/diary-events`, `/life-events`, `/labs`
- `POST /integrations/telegram/webhook`, `/integrations/garmin/sync`
- `GET /integrations/{provider}/status`, disconnect/revoke endpoints
- `GET /analytics/{metric}`, `POST /reports`, `GET /reports/{id}`
- `POST /exports`, `POST /imports`, `GET /data-quality`
- `GET /health/live`, `GET /health/ready` without sensitive output

Validate units/timestamps, cap payloads, rate-limit authentication/Telegram/report/AI operations, and verify Telegram webhook secrets.

## 8. Integrations

### Telegram

- Dedicated bot, allow-listed owner chat/user ID, HTTPS secret-token webhook in production.
- Deduplicate provider update IDs; minimize retention of raw chat text.
- Flow: receive → extract → validate → show draft → confirm/edit/cancel → store → return record link.
- Never send full medical history to a group chat.
- Support safe manual commands and graceful Ollama failure.

### Garmin

Begin with a feasibility Beads spike. Verify current Garmin developer eligibility, terms, scopes, authentication, webhooks/pull behavior, rate limits, retention rules, and which metrics are actually available. Do not promise unavailable fields.

Map sleep start/end and score, heart rate, activity/intensity, HRV, stress, and body battery when available. Preserve provider IDs/revisions, support backfills and incremental cursors, reconcile revised data, and show gaps honestly. Encrypt tokens and make disconnect/provider-data deletion easy.

### Location, timezone, and weather

Prefer timezone from the event/device and coarse user-entered location. Exact coordinates are opt-in. IP location is low-confidence and opt-in. Resolve event timezone at event time and preserve it after travel. Store weather provider, query time, source observation ID, and confidence. Let the user disable or delete enrichment independently.

### Labs

Start with reviewed manual entry and CSV import mapping. Store original values, units, and lab-specific reference ranges; normalized units are separate derived fields. Flag ambiguous parses. Defer FHIR or HealthKit import until the core is stable.

## 9. Local LLM pipeline

### Extraction

1. Minimize irrelevant input.
2. Send text, known medications, current timezone, and strict JSON Schema to local Ollama.
3. Validate output with Pydantic and reject unknown types/units.
4. Run deterministic checks for negation, date ambiguity, implausible amounts, duplicates, and conflicting medication names.
5. Show field confidence and a structured draft.
6. Persist only appropriately confirmed facts.

Maintain a versioned evaluation set covering relative dates, overnight events, travel/DST, fractions, corrections, multiple events, negation (“did not take”), hypotheticals, and prompt injection in diary text.

### Analysis

Compute totals, comparisons, rolling summaries, and chart datasets in deterministic code. The LLM may summarize computed results and selected facts; it should cite source record IDs and omit unsupported claims. Store model name/digest, prompt/schema version, input IDs, generation time, and settings. Reports exclude AI by default and provide regenerate/hide/delete controls.

## 10. Web pages

- **Today:** plan schedule, actual doses, symptoms, open episode, sleep/activity context, quick add, emergency-plan link.
- **Timeline:** unified, filterable facts with source, timezone, confirmation, and correction indicators.
- **Medication plan:** current approved version, slots/instructions, approval provenance, history, and version diff.
- **Doses:** actual-versus-plan timeline, daily totals, missed/late annotations, stress markers.
- **Stress episodes:** builder, linked events, triggers, recovery, comparison.
- **Symptoms and diary:** severity trends, tags, private-entry settings.
- **Sleep, vitals, activity:** daily/weekly views, availability and gap indicators.
- **Labs:** analyte trend, original range bands/units, table and source metadata.
- **Analytics:** user-selected overlays with metric definitions and correlation cautions.
- **Reports:** range/section selection, preview, labeled fact/plan/AI sections, PDF/CSV/JSON.
- **Data quality:** duplicates, ambiguous drafts, missing units/timezones, integration gaps.
- **Settings/privacy:** integrations, retention, location precision, export/deletion, security, backups, audit.
- **Emergency plan:** physician-authored dated instructions, contacts, fast injection logging, emergency-services reminder; works without AI.

Use a mobile-first responsive layout, keyboard navigation, screen-reader labels, visible focus, large touch targets, and chart table/text alternatives. Never distinguish fact/plan/AI by color alone.

## 11. Analytics and physician reports

Initial deterministic metrics:

- Daily actual medication total compared with the active plan.
- Timing difference from scheduled slots using a documented tolerance.
- Stress/up-dose episode count, duration, extra dose, triggers, and reported recovery time.
- Symptom frequency/severity and user-selected windows around doses.
- Sleep duration/score, heart-rate summaries, available HRV/stress/body-battery, activity/intensity, and missingness.
- Lab trends with original ranges/units.
- Overlays for illness, travel, weather, activity, and life events.

Do not add inferential “insights” without sample size, missingness, confounding, and multiple-comparison disclosures.

A physician report should include reporting period; current approved regimen and source date; actual-dose totals/timing; stress and injection episodes; symptoms/function; selected wearable/lab trends and data availability; significant-event timeline; patient questions; optional separately boxed AI observations; provenance, definitions, timezone, and generation time. Reports should be concise, printable, and accompanied by detailed CSV/JSON when requested.

## 12. Security and privacy

- Threat-model account takeover, public hosting, stolen tokens/backups, malicious webhooks, prompt injection, dependency compromise, and shared devices.
- Use passkeys or strong passwords plus MFA, secure HTTP-only same-site cookies, CSRF protection, expiry/revocation, and login throttling.
- TLS everywhere; expose only 80/443. Keep PostgreSQL, Redis, Ollama, and administration private.
- Encrypt integration tokens and sensitive fields with keys outside the database; encrypt disks and backups.
- Keep secrets out of Git, issue bodies, logs, prompts in fixtures, screenshots, and browser bundles.
- Sanitize imported, diary, and LLM-rendered content against XSS; treat all text as untrusted.
- Use least privilege, pinned/patched dependencies, container/non-root users, and CI scanning.
- Redact logs; do not log tokens, raw Telegram bodies, exact location, labs, or free-text health data by default.
- Provide complete export, integration disconnect, retention controls, and account/data deletion.
- Audit logins/security changes, plan approval changes, corrections, exports, and reports.
- Reassess regulatory and legal obligations before adding clinicians, multiple users, or commercial use.

## 13. Hosting, deployment, backups, and observability

Use a dedicated personal subdomain, Docker Compose, Caddy-managed TLS, pinned images, distinct dev/staging/production secrets, and controlled Alembic migrations. Keep staging synthetic. Ollama may run beside production on a private network or on a trusted home machine through a private tunnel; it must never be directly public.

Backups:

- Encrypted nightly PostgreSQL logical backup plus any justified volume/base backup.
- Practical 3-2-1 copies, including offsite storage under separate credentials.
- Example retention: 7 daily, 5 weekly, 12 monthly, adjusted for privacy/storage.
- Include report artifacts, uploads, restore configuration, and a separately protected key-recovery process.
- Automated integrity checks plus quarterly isolated full restore drills.
- Initial targets: RPO 24 hours and RTO 4 hours.

Observability:

- Structured redacted logs with correlation IDs.
- Request errors/latency, auth failures, queue age, Telegram failures, Garmin sync lag, import counts, LLM schema failures/latency, report failures, database/storage health, backup age, and certificate expiry.
- Alerts for unavailability, repeated authentication attack, stopped import, stale/failed backup, restore-check failure, or low disk.
- Liveness and readiness endpoints must expose no health data.
- Maintain incident, restore, key-loss, device-loss, and provider-compromise runbooks.

## 14. Testing

- Unit tests: decimal doses/units, plan effective dates, schedule matching, DST/travel, correction history, metrics, safety-label rendering.
- Property tests: event windows, totals, idempotency, import ordering/revisions, and non-overlapping plan rules.
- Database/integration tests: migrations from prior versions, constraints, Telegram secret/deduplication, Garmin reconciliation fixtures, weather/lab mappings.
- LLM evaluation: gold cases, field accuracy thresholds, ambiguity/negation/hallucination/prompt-injection tests, and regression gates for model/prompt changes.
- Security/API tests: ownership, CSRF, sessions, rate limits, invalid units, malicious HTML, payload limits, and secret/log redaction.
- End-to-end tests: Telegram confirm, web correction, plan transition, episode, injection, import, chart, report, export/deletion.
- Accessibility/visual tests: keyboard journey, automated audit, responsive UI, chart alternatives, and rendered PDF inspection.
- Operations tests: backup/restore, dependency outage, retry/dead-letter behavior, expired-token recovery, and emergency page with AI/integrations offline.

Use synthetic health data in CI and demos.

## 15. Phased roadmap and acceptance criteria

### Phase 0 — Foundation and safety

Deliver repository, verified Beads workflow, architecture decisions, safety specification, threat model/data classification, local stack, CI, schema prototype, and low-fidelity UI map.

Acceptance:

- Fact/plan/AI separation and prohibited AI actions are documented and testable.
- Core safety and emergency behavior is specified.
- Local setup and CI lint/type/unit checks work.
- Secrets/real health data are absent from Git/logs/fixtures.
- Each later deliverable has a Beads issue with dependencies and acceptance criteria.

### Phase 1 — Trusted local record

Deliver authentication, PostgreSQL/migrations, medication and plan versions, doses, symptoms, diary/life events, episodes, injections, corrections/audit, web forms, timeline, and raw export.

Acceptance:

- Plan versions can be drafted, approved with provenance, retired, diffed, and historically queried.
- Actual doses remain independent and compare correctly with the plan for a date/timezone.
- Every core event can be added, viewed, corrected with retained history, filtered, and exported.
- Emergency injection logging is fast, confirmed, auditable, and AI-independent.
- Decimal/unit, ownership, DST/travel, and correction tests pass.

### Phase 2 — Telegram and local extraction

Deliver bot/webhook security, allow-list, deduplication, Ollama adapter, schemas, confirmation/edit flow, fallback commands, and evaluation harness.

Acceptance:

- Multi-event messages create valid drafts and persist nothing prematurely.
- Ambiguous medication/amount/time is visibly flagged.
- Duplicate updates cannot duplicate records.
- Model outage or invalid JSON falls back safely.
- Defined per-field gold-set thresholds pass without sensitive logs.

### Phase 3 — Dashboard, analytics, labs, and reports

Deliver Today, advanced timeline, plan comparison, episode review, lab entry/import, charts, deterministic analytics, data-quality page, report snapshots, and PDF/CSV/JSON.

Acceptance:

- Charts show metric definition, range/timezone, source/missingness, and accessible alternative.
- Dose totals/timing match independent fixtures.
- Reports visually separate facts, plan, patient notes, and optional AI; AI defaults off.
- Historical report snapshots retain source manifests and remain reproducible.
- Representative PDFs pass visual/print review.

### Phase 4 — Garmin and context

Deliver authorized Garmin sync for available metrics, backfill/reconciliation, integration health, timezone/location controls, and weather enrichment.

Acceptance:

- Imports are idempotent; provider revisions reconcile and gaps remain visible.
- Unsupported metrics say unavailable, never zero.
- DST and travel cases preserve correct event time.
- Exact location is opt-in and context enrichment can be deleted independently.
- Token refresh, rate limits, timeouts, backfill, and partial failures pass tests.

### Phase 5 — Production on personal domain

Deliver hardened infrastructure, domain/TLS, monitoring, alerts, encrypted backup/restore, privacy controls, and release checklist.

Acceptance:

- Only HTTPS is public; data services and Ollama are unreachable publicly.
- MFA/passkeys, sessions, rate limits, audit, secrets, and redaction are verified.
- An encrypted backup restores within RTO/RPO targets in isolation.
- Controlled tests trigger outage, stale-backup, failed-integration, and disk alerts.
- Export, disconnect, and deletion workflows pass.

### Phase 6 — Hardening and longitudinal learning

Deliver performance/accessibility improvements, stronger model evaluation, carefully framed exploratory analytics, refined reports, and operational drills.

Acceptance:

- Common views meet defined latency targets on realistic volumes.
- No critical accessibility blockers remain.
- AI cites inputs, discloses missingness, passes regression gates, and cannot mutate facts/plans.
- Restore and incident drills are scheduled, executed, and tracked in Beads.

## 16. Mandatory Beads workflow

### Initialize and document the installed version

The building agent must create the first tracked task for verifying Beads itself. Illustrative discovery only:

```bash
command -v bd || command -v beads
bd --version
bd --help
bd init --help
bd init
```

After verification, add `docs/beads-workflow.md` with the exact supported commands for initialization, epic/issue creation, dependencies, ready-work query, status update, comments/evidence, close/reopen, and sync/repair if applicable. Commit repository state that the installed Beads documentation says to commit; exclude secrets, caches, and machine-specific files.

### Epics and mapping

Create one epic for every roadmap phase:

| Epic label/title | Scope |
|---|---|
| `HC-P0 Foundation` | safety, threat model, architecture, repo/CI, Beads, schema/UI spikes |
| `HC-P1 Trusted record` | auth, plans, doses, symptoms/diary, episodes, injections, audit/timeline/export |
| `HC-P2 Telegram + AI extraction` | bot, Ollama, schemas, confirmation, evaluation |
| `HC-P3 Dashboard + reports` | Today, analytics, labs, charts, data quality, PDF/exports |
| `HC-P4 Integrations` | Garmin, reconciliation, timezone/location/weather |
| `HC-P5 Production` | domain/TLS, hardening, monitoring, backups/restore, release |
| `HC-P6 Hardening` | performance, accessibility, AI regression, analytics, drills |

Use the actual IDs assigned by Beads. Suggested labels include `phase:P0`–`phase:P6`, `area:meds`, `area:events`, `area:web`, `area:telegram`, `area:garmin`, `area:ai`, `area:reports`, `area:ops`, `risk:clinical`, `risk:privacy`, `risk:security`, `type:spike`, `type:bug`, and `type:chore`.

Use only supported statuses, mapping them in the workflow document to the concepts `backlog`, `ready`, `in_progress`, `blocked`, `review`, and `done`. Use actual supported dependency relationship names; conceptually distinguish required `blocks/blocked-by` relationships from informational links.

### Issue rules

Each issue must contain:

- A concrete user or operational outcome.
- Scope and explicit exclusions.
- Observable acceptance criteria.
- Dependencies and risk labels.
- Test/verification requirements.
- Security, privacy, clinical-safety, migration, and observability considerations where relevant.
- Completion evidence before closure.

Prefer small vertical slices—schema, API, UI, and tests for one capability—over enormous layer-based tasks. Create spikes for unknowns that can invalidate work, especially Garmin access and production-to-local-Ollama topology.

### Required working loop

1. Query Beads for ready/unblocked work in the active epic.
2. Select the highest-priority issue that fits current capacity.
3. Verify dependencies, risks, and acceptance criteria; split it if too broad.
4. Mark it in progress and record the intended approach.
5. Implement the smallest complete vertical slice with tests.
6. Record discoveries, decisions, scope changes, and blockers in Beads.
7. Create and link new issues for newly discovered work.
8. Run acceptance checks and attach concise evidence: tests, screenshots, report render, migration, security check, or restore result as applicable.
9. Move through review when required and close only when criteria pass.
10. Pull the next ready issue from Beads.

Illustrative operations—not verified syntax:

```bash
bd create --type epic --title "HC-P1: Trusted local health record"
bd create --type task --title "Version physician-approved medication regimens"
bd dep add CHILD_ID --blocked-by SCHEMA_ID
bd update ISSUE_ID --status in_progress
bd show ISSUE_ID
bd update ISSUE_ID --status review
bd close ISSUE_ID
bd list --status ready
```

### Definition of ready

- Outcome and observable acceptance criteria are clear.
- Dependencies are resolved or intentionally parallelizable.
- Data, security, privacy, and clinical-safety impact is identified.
- Any uncertainty capable of invalidating the work has a completed spike.

### Definition of done

- Acceptance criteria and relevant normal/failure/safety tests pass.
- Evidence is recorded in Beads.
- Migrations, OpenAPI, UI/accessibility, observability, recovery, and documentation are updated as applicable.
- No secret or personal health data appears in code, logs, fixtures, screenshots, or issue text.
- Deferred work has linked issues; closing does not conceal incomplete scope.
- Parent epic status accurately reflects its children.

### Initial Beads backlog

Create these immediately after the CLI workflow is verified:

1. `HC-P0 Foundation` epic.
2. Verify Beads version, initialize it, and document exact repository workflow.
3. Safety specification: fact/plan/AI boundaries, confirmation rules, emergency behavior, prohibited AI actions.
4. Threat model and data classification for Telegram, Garmin, location, reports, backups, and model input.
5. Architecture decision: PostgreSQL, deploy topology, and private Ollama connectivity.
6. Canonical event/time/correction/provenance schema spike.
7. Medication, regimen-version, actual-dose, stress-dose, and injection schema.
8. Repository/CI bootstrap with pinned dependencies, synthetic fixtures, lint/type/test/security checks.
9. UI information architecture for Today, timeline, plan, episode, emergency, reports, data quality, privacy.
10. Garmin feasibility spike verifying current access, metrics, terms, authentication, limits, retention, and test environment.
11. Telegram/Ollama synthetic-data proof of concept for schema-constrained draft and safe failure.
12. Backup/restore design defining RPO/RTO, encryption/key recovery, retention, and restore test.

Phase 1 must depend on the safety specification and canonical schema. Telegram implementation must depend on the trusted record plus the extraction evaluation design. Garmin implementation must depend on its feasibility spike. Production launch must depend on security review and a successful isolated restore drill.

## 17. Risks and mitigations

- Clinical misunderstanding: explicit category separation, confirmations, plan provenance, deterministic emergency view.
- AI hallucination/prompt injection: untrusted-input isolation, schema validation, deterministic checks, citations, no direct fact/plan mutation.
- Wearable incompleteness: feasibility spike, missingness indicators, reconciliation, no zero filling.
- Timezone mistakes: UTC plus original local/IANA timezone, DST/travel fixtures, timezone-visible reports.
- Sensitive-data exposure: private services, least privilege, encryption, redacted logs, coarse location default, restore-safe key management.
- Project overreach: modular monolith, vertical phases, Beads definitions of ready/done, defer native apps and prediction.
- False confidence in backups: integrity checks and repeated isolated restore drills.
- Analytics overinterpretation: deterministic definitions and explicit missingness, confounding, sample-size, and correlation cautions.

## 18. Future enhancements

- Apple Health/HealthKit and additional wearable imports.
- FHIR clinical/lab import and patient-controlled sharing.
- Offline-capable PWA, then native/watch capture if justified.
- Medication inventory and refill reminders.
- Travel/calendar context with explicit consent.
- Patient-defined experiments with preregistered questions and careful statistics.
- Expiring encrypted clinician share links only after a multi-user security/compliance redesign.
- Local voice capture/transcription.
- Validated alerts only with appropriate clinical governance and evidence.
- Condition-specific modules built around the stable event core.

## 19. First meaningful milestone

The first milestone is a trustworthy local record—not an AI dashboard. It must version the physician-approved regimen, record actual doses and symptoms, represent stress and emergency events, preserve corrections/provenance/timezones, and export the data. Once that foundation meets Phase 1 acceptance criteria, Telegram and Qwen can safely improve capture; dashboards, integrations, and analysis can then build on dependable facts.
