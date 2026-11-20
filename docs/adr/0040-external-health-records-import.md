# ADR-0040: Read-only import of the owner's external health-records dataset

**Status:** Accepted — 2026-11-20 (owner approved, with the time-zone and report-time
changes recorded in section 4).

## Context

The owner keeps a separate collector project that pulls their records from patient
portals (NIH Clinical Center FollowMyHealth; Johns Hopkins MyChart, including records
shared from Mayo Clinic and UMMS through Care Everywhere) and from PDFs dropped into an
Inbox folder. It writes one SQLite database, `data/health.db`, plus the original files.
Its `DATASET.md` (schema version 2) is the contract for consumers. HealthCurve is meant
to be where that data is seen and queried; the collector stays the only writer.

The dataset holds about 5.6k lab values, 116 imaging studies, 224 encounters, 1.77k
documents, 59 medication-list rows, and smaller condition, immunization, allergy,
vitals and surgical-history tables. Every row has a `record_id` that is stable across
rebuilds and unique per table, plus `source` (the channel) and `organization` (the
health system that produced it). Each rebuild is a full snapshot; there is no delta feed.

It is the most sensitive data HealthCurve would hold. Clinical notes, letters and
After Visit Summaries are free text written by clinicians; family history names
relatives; the condition list's status field can contain the owner's own portal
comments. The chat is deliberately limited today: it sees only the medications the
owner put in the plan vocabulary, through the curated `analytics` views of ADR-0036.
Simply loading this dataset into `fact.lab_*` would put 5.6k lab values into
`analytics.lab_results` and the day analysis the moment the import ran.

## Decision

### 1. An integration adapter, not a new layer

The importer lives in `healthcurve.integrations.health_records`, next to Garmin. It
sits above `labs` in the import-linter stack, so it can write lab facts through
`labs.service` without any contract change. `cli` and the Make target wire it up.

### 2. Reading the snapshot safely

- The dataset root is mounted read-only, for example
  `/Users/jeff/Documents/Export-NIH-Data:/import/health-records:ro`, configured as
  `HC_HEALTH_RECORDS_ROOT`. Nothing is ever written there.
- The importer opens `file:…/data/health.db?mode=ro` (never `immutable=1`) and uses
  SQLite's online backup API to copy it into a private temp file. Every read uses that
  copy. `dataset_meta.generated_at` is read from the source before and after the copy;
  if it changed, the run retries once and then fails without writing.
- `dataset_meta.schema_version` must be `2`. Any other value, a missing required
  column, or a missing/duplicate `record_id` refuses the import before any write.
  Undocumented extra columns (today `lab_results.comment` and the condition codes) are
  kept in the raw source row but are not interpreted.
- `--dry-run` reads and diffs the snapshot and reports per-table counts
  (new / changed / unchanged / no longer in source) with no writes.

### 3. Import batches and record identity

`fact.health_record_import_batch` holds one row per run: owner, schema version,
`generated_at`, the `exported_at:<source>` values, the copy's SHA-256, start/finish
time, status, and per-table counts. Re-running an identical snapshot creates a batch
with every count in "unchanged".

`fact.health_record` is the identity envelope, one row for every dataset row ever seen,
unique on `(owner_id, dataset_table, record_id)`. It holds `source`, `organization`, the
parsed date when the source gives one plus the raw date text, a display title, the
category used for chat visibility (section 5), its state (`present` or
`no_longer_in_source`), the first and last batch that saw it and the batch that stopped
seeing it, and its current revision.

`fact.health_record_revision` is append-only. A new revision is written only when a
row's content hash changes. It keeps the clinician columns as typed columns
(`provider`, `ordered_by`, `reading_provider`, `performing_lab`, `prescriber`), the
long text (`text`, `report_text`, `impression`) as text columns, relative file paths,
and the complete raw source row as JSONB for audit. Corrections are therefore never
overwrites. The provenance a reader needs (`organization`, date, clinicians) is in
typed columns, not only in JSON.

An ID missing from a new snapshot is marked `no_longer_in_source`, never deleted. If it
comes back, it returns to `present` and the revision history shows what happened.

### 4. Labs go into the existing lab facts

Each source lab panel becomes a `fact.lab_panel`, so imported results appear on the
Labs page beside owner-entered ones.

- **Panel grouping.** The dataset has no panel or accession ID, so a panel is
  `(source, organization, datetime, panel)`. Its `provider_id` is a hash of that key,
  `source_type` is the new `health_records_import`, and `confirmation_state` is
  `provider_imported`. The existing provider-identity index on `lab_panel` isn't
  owner-scoped, so the owner ID is part of the hash.
- **Values.** `test` becomes `analyte_name` (the lab's own name). `value_text`,
  `units`, `ref_range` and `flag` map to the existing original-value, unit, range and
  provider-flag columns unchanged. Normalization runs the existing versioned registry,
  which converts only listed units and never merges unconvertible units. It tries
  `test` first, then `test_normalized`, and never the specimen-suffixed names such as
  `Glucose (urine)`. The dataset's `test_normalized` is kept in a new nullable
  `lab_result.source_normalized_name` column for display and later charting.
- **Provenance.** New nullable `lab_panel.source_organization` and
  `lab_panel.ordered_by` columns. `laboratory_name` is `performing_lab` when given.
  Each result links to its `health_record` envelope by `record_id`.
- **Time.** The dataset stores local wall-clock time with no zone. The owner fixed the
  zone per producing organization: NIH Clinical Center, Johns Hopkins Medicine and UMMS
  are `America/New_York`; Mayo Clinic is `America/Chicago`. Any other organization
  (Inbox uploads) falls back to `America/New_York`, and the envelope records that the
  zone was assumed rather than mapped. A date-only row is placed at local noon and
  flagged as date-only.
- **Report time = collection time.** The dataset has collection time only, and the
  report date isn't clinically important to the owner. The importer sets
  `lab_panel.reported_*` equal to the collection time, so the lab schema's required
  report time and its `reported_at >= occurred_at` check stay unchanged.
- **Corrections.** When the present results of a panel change, a new panel revision
  supersedes the old one through the existing correction chain, with
  `source_revision` set to the content hash. Results that are no longer in the source
  stay in history. Current views exclude results whose envelope is
  `no_longer_in_source`, and the Labs page can show them with a marker.

### 5. Privacy: hidden from chat and AI by default, opt-in by category

- **New table `ops.health_record_ai_visibility`** `(owner_id, category, enabled,
  changed_at)`. A missing row means hidden. The categories are:
  - `labs`
  - `imaging_impressions`
  - `imaging_reports`
  - `encounters`
  - `conditions`
  - `immunizations`
  - `allergies`
  - `vitals`
  - `surgical_history`
  - `clinical_notes`
  - `visit_summaries`
  - `letters_and_admin`
  - `other_documents`

  Every toggle is audited.
- **Medications have no category and cannot be opted in.** Imported medication-list
  rows live only in `fact.health_record*`. They never touch `plan.medication`,
  regimens, dose events, the Doses page, `analytics.medications` or the cortisol
  models. The UI labels them "Medication lists as recorded by each organization —
  reference only, not your plan or doses." Changing this would need a new ADR.
- **Database enforcement, not just code.** The analyst roles of ADR-0036 get no
  grant on `fact.health_record*`. `analytics.lab_results` is redefined to exclude
  `source_type = 'health_records_import'` unless the owner has enabled `labs`, and
  always to exclude results that are no longer in the source. The day analysis
  (ADR-0020) applies the same filter in code. A Postgres test checks that the analyst
  roles cannot read the new tables, and that imported labs are invisible in the view
  until the toggle is on.
- **Imaging impressions view (deferred).** A gated
  `analytics_text.imaging_impressions` view (date, organization, modality, exam,
  impression, `is_outside_import`) is the next opt-in. It changes the analytics
  catalog, whose version pins the recorded chat evaluations, so it ships with its own
  catalog version and re-recorded local-model evals. Until then, `labs` is the only
  category with a chat path, and the Settings page says so for every other one.
- **The AI role.** Default privileges give the AI role SELECT on every `fact` table and
  write access to `ops`. The migration revokes its access to `fact.health_record*` and
  leaves it read-only on `ops.health_record_ai_visibility`, so no AI code path can
  switch a category on.
- **Physician reports** exclude imported records in this phase. Including them is a
  follow-up with its own owner choice.
- **Other paths.** Document text is shown as plain text and never rendered as HTML.
  Logs record only counts and IDs. Private export always includes envelopes, batches
  and opt-ins. Revisions, which hold full clinical text, are included only when the
  export includes sensitive content. Account deletion covers the new tables, and backups
  include them because they are ordinary Postgres tables.

### 6. Re-running

The import runs on demand, never in the background:

- `python -m healthcurve.cli import-health-records [--dry-run] [--dataset-root PATH]`
- `make import-health-records`, which runs the same command through
  `docker compose run --rm api …`, so the read-only mount and the database role match
  production.

Each run is one batch, and concurrent runs are blocked by a Postgres advisory lock.
`docs/health-records-import.md` documents setup, the mount, what each table becomes,
and the visibility toggles.

### 7. Read API and UI

- The API is read-only:
  - `GET /health-records/batches`
  - `GET /health-records` (filter by table, organization, date, state; paginated)
  - `GET /health-records/{id}` (current revision plus revision history)
  - `GET`/`PUT /health-records/ai-visibility`

  `PUT` requires CSRF and is audited.
- **Labs page.** Shows "From" (organization) and the panel name, and marks results that
  are no longer in the source. Those results stay in the table but aren't charted. The
  results list now shows only the current version of a corrected panel.
- **Health records page** (read-only). Imaging, visits, documents, conditions,
  immunizations, allergies, vitals, surgical history and the reference medication
  lists, each showing "From: organization", date, clinicians and state.
- **AI visibility.** Set per category on Settings. Every category starts off, and
  medications are not listed.
- **Original files** (PDF/HTML) are served from the read-only mount through an
  owner-authenticated endpoint that resolves paths only inside the dataset root
  (traversal-checked, no symlink escape). HTML is downloaded, never inlined.

## Consequences

- Imported facts are clearly separated by `source_type`, and their provenance is
  visible everywhere they appear.
- The chat's medication boundary is unchanged by construction.
- Opting a category into AI is one audited switch, enforced in SQL views that the
  analyst roles read.
- The import never alters the dataset. A disappearing ID becomes a state change, not a
  data loss.
- Panel grouping and the time zone are inferred. Both are documented, shown in the UI,
  and reversible: a future contract field (panel or accession ID, time zone) would
  re-key panels through ordinary corrections.
- An imported panel's report time is its collection time, by owner decision. It is not
  the laboratory's report time.
- Imported vitals stay reference records. They are not merged into HealthCurve's
  blood-pressure, weight or temperature facts, so the two never double count.

## Gaps in the dataset contract (for the collector project)

1. `lab_results.comment`, `conditions.resolved_date`, `conditions.icd_code`,
   `conditions.snomed`, and all `vitals` columns exist in `health.db` but are not
   described in `DATASET.md`.
2. There is no report or result time for labs, only collection time.
3. There is no panel, order or accession identifier for `lab_results`, so consumers
   must infer grouping.
4. There is no per-row time zone, and for Care Everywhere records it is unknown whether
   times were converted. `generated_at` also has no zone.
5. Family and social history are named as sensitive, but no table or document
   `category`/`kind` identifies them, so a consumer cannot hide them separately from
   other clinical documents.
6. `conditions.status` can contain free-text patient comments, which makes it free
   text rather than an enumerated status.
7. Imaging `report_file`/`pdf_file` and `documents.file_path` may refer to the same
   file. A shared key would let consumers de-duplicate.

## Alternatives considered

- **Query `health.db` live from HealthCurve.** Rejected: the collector rewrites it in
  place, it can't join with HealthCurve facts or carry visibility state, and it would
  bypass the analytics-role boundary.
- **One typed table per dataset table.** Rejected for now: nine near-duplicate
  revision schemes for mostly text rows. The envelope plus revision keeps clinician
  and date provenance typed. Labs, the only table with numeric analysis, go into typed
  lab facts.
- **Mapping medication rows into `plan.medication` as inactive.** Rejected: it would
  enlarge the chat's medication vocabulary and blur reference history with the plan.
- **Hard-deleting rows that disappear.** Rejected: portals retract and correct
  results, and the history matters.
