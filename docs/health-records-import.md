# Importing your health-records dataset

HealthCurve can read the health-records dataset that a separate collector project builds
from your patient portals and your Inbox PDFs. The collector is the only writer.
HealthCurve reads one snapshot at a time and never changes the dataset. The design and
its privacy rules are in [ADR-0040](adr/0040-external-health-records-import.md). The
dataset's own contract is `DATASET.md` in the collector project (schema version 2).

## Set up the read-only mount

1. In `.env`, point `HC_HEALTH_RECORDS_DIR` at the dataset root, which is the folder
   that holds `data/health.db`:

   ```bash
   HC_HEALTH_RECORDS_DIR=/Users/you/Documents/Export-NIH-Data
   ```

2. Recreate the API container so the mount applies. Compose mounts the folder
   **read-only** at `/import/health-records` and sets
   `HC_HEALTH_RECORDS_ROOT=/import/health-records`. If the variable is empty, Compose
   mounts an empty `./var/health-records`, and the import reports that no dataset was
   found.
3. Run the migrations (`make migrate`) so the import tables exist.

## Run the import

From the app, use **Health records** (or **Settings & privacy**) → **Import from your
patient portals**:

- **Check for changes** diffs the snapshot and writes nothing.
- **Import now** runs one import batch.

Both are CSRF-protected and rate-limited (30 runs an hour), and both show per-table
counts. Or from a terminal:

```bash
make import-health-records-dry-run   # shows what would change; writes nothing
make import-health-records           # one import batch
```

Both targets run `python -m healthcurve.cli import-health-records` inside the API
container. Outside Docker, pass `--dataset-root PATH` or set `HC_HEALTH_RECORDS_ROOT`.
The command prints one row per dataset table: new, changed, same, gone (no longer in
the source), and back (returned after being gone). It also prints how many lab panels
were created or corrected.

Re-run the import whenever the collector has rebuilt the dataset. Each run:

- **Reads a consistent copy.** It opens `data/health.db` with `mode=ro` and copies it
  with SQLite's backup API into a private temp file. It then checks that
  `dataset_meta.generated_at` didn't change during the copy. If a rebuild landed
  mid-copy, it retries once, then stops without writing.
- **Checks the contract.** The import refuses any `schema_version` other than 2, any
  missing table or documented column, and any missing, over-long or repeated
  `record_id`. Columns the contract doesn't document are kept in the stored source row
  but not interpreted.
- **Is one import batch.** The batch stores `generated_at`, each source's
  `exported_at:<source>`, the snapshot's SHA-256 and the per-table counts. Re-running
  an unchanged snapshot adds a batch whose counts are all "same".
- **Upserts by `record_id`.** A changed row gets a new revision, and earlier revisions
  are kept. A row missing from the snapshot is marked **no longer in source** and never
  deleted. If it reappears, it is marked present again.

Two imports can't run at once: a Postgres advisory lock serializes them.

## What each table becomes

| Dataset table | In HealthCurve | Shown on |
|---|---|---|
| `lab_results` | Lab panels and results (plus a source record per row) | Labs |
| `imaging_studies` | Source record: impression, report text, ordering and reading clinicians | Health records → Imaging |
| `encounters` | Source record: visit, department, clinician, note and AVS files | Health records → Visits |
| `documents` | Source record: category, kind, author, extracted text | Health records → Documents |
| `conditions`, `immunizations`, `allergies`, `vitals`, `surgical_history` | Source records | Health records |
| `medications` | Source record, **reference only** | Health records → Medication lists |

Every record keeps `organization`, shown as **From**, and `source`, kept for audit and
shown as "Received through". It also keeps the clinician columns as the source wrote
them. Original files can be downloaded from the record. They are served only from paths
inside the dataset root, always as downloads, and never rendered inside HealthCurve.

### Labs

- **Panels.** A panel is one `(source, organization, datetime, panel)` group, because
  the dataset has no accession number.
- **Values.** The lab's own name, value, unit, range and flag are stored unchanged. The
  versioned normalization registry ([lab normalization](lab-normalization.md)) adds a
  derived value only for listed units. It tries the lab's name first, then the dataset's
  `test_normalized`. A specimen-suffixed name such as `Glucose (urine)` is never
  normalized, so urine values never join blood trends. Values in different units are
  never merged.
- **Time.** The time zone is set per organization, as the owner decided:

  | Organization | Time zone |
  |---|---|
  | NIH Clinical Center, Johns Hopkins Medicine, UMMS | America/New_York |
  | Mayo Clinic | America/Chicago |
  | Any other | America/New_York, marked "zone assumed" |

  A repeated fall-back hour takes its first occurrence.
- **Report time.** The dataset has collection time only, so the report time is set to
  the collection time.
- **Corrections.** When a panel's source content changes, the new version supersedes
  the old through the normal correction chain. The Labs page shows the current version,
  and the earlier one stays in history.
- **Withdrawn results.** Results that are no longer in the source stay on the Labs page
  with a marker. They are not charted.
- **Seeing every result.** Labs → **Show one test** lists every test with its result
  count. Choosing one lists only that test and charts its whole history across every
  organization. Tests outside the curated registry, such as ACTH, are charted in the
  lab's own unit, one chart per unit. Without a test chosen, charts cover only the
  current page. The picker uses an opaque key, so test names never appear in URLs or
  access logs.
- **Grouped picker and "About this test".** The test picker groups tests by category
  (adrenal and pituitary, thyroid, chemistry, blood counts, urine, pulmonary function
  tests, ECG and echo, and others), alphabetically within each group. Choosing a test
  shows a plain-language description of what it measures and why it is checked, from
  the fixed, versioned guide in `frontend/src/labTestGuide.ts` (`hc-lab-test-guide-v1`),
  with a link to the matching MedlinePlus page. The guide is general education. It
  never interprets the owner's values and no model writes it.
- **Specimens on charts.** The dataset marks every non-blood specimen in the test name,
  for example `Glucose (urine)`. So an imported result with no recorded specimen and no
  such suffix charts as **Blood**, together with results recorded as "Blood (…)". NIH
  records no specimen at all. The table still shows each specimen exactly as recorded.
- **Procedure markers.** Surgeries from the Procedures page are drawn on lab charts as
  dashed lines, between the results before and after them. Points are evenly spaced, so
  a line shows order, not elapsed time.
- **Rows without a value.** Source rows with no value at all are kept as source records
  only.

## Chat and AI: hidden until you opt in

Nothing imported is visible to chat or AI by default. Settings & privacy → **Imported
health records in chat and AI** has one switch per category. Each change is audited.

- **Labs** is the only category with a chat path today. When it's on,
  `analytics.lab_results` (what chat reads) includes imported results. Results that are
  no longer in the source are always excluded. The day analysis applies the same rule.
- **Every other category** is stored for later. Switching one on has no effect until a
  gated view is added for it.
- **Medication lists have no switch.** They never enter your plan, your doses, the
  chat's medication list or the cortisol models.
- **Database enforcement.** The analyst roles that chat queries through have no access
  to the imported-record tables. The AI role can't read them either, and it can read
  the switches but never change them.
- **Physician reports** leave imported labs out for now.

## Export, deletion and backups

- **Private export.** Records, batches and switches are always included. Revisions,
  which hold full clinical text, are included only when the export includes sensitive
  content.
- **Account deletion.** It removes all imported data, including lab correction chains.
- **Backups.** They include the tables like any others.

## Gaps in the dataset contract

These are listed in ADR-0040 for the collector project:

- some columns exist in `health.db` but are undocumented;
- labs have no report time and no panel or accession ID;
- rows have no time zone;
- family and social history can't be identified separately;
- condition status can hold free text;
- files have no de-duplication key.
