# ADR-0023: Retain exact wearable facts in PostgreSQL without automatic expiry

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

HealthCurve is a private, single-owner application whose dense Garmin observations are
recorded facts. Deleting or lossy-compacting old samples would weaken corrections,
selected-day review, complete export, and future reinterpretation. Cold storage would
add encryption keys, manifests, retrieval jobs, and another deletion/restore path to a
system that already has encrypted nightly backups.

The five-year synthetic workload contains 3,690,540 metric rows. Its hot relation was
2,379,350,016 bytes. A least-privilege PostgreSQL custom dump was 230,389,883 bytes and
took 11.632 seconds; restoring it into a second isolated PostgreSQL container took
17.833 seconds. The restored relation was 2,116,419,584 bytes. All rows matched the
source by count and deterministic whole-row signature, and the restore sentinel
matched. Both databases and all health values were synthetic and were destroyed.

## Decision

Retain current and superseded raw wearable facts indefinitely in PostgreSQL. Do not
automatically expire, downsample, move, or destructively compact them. Versioned daily
summaries are performance caches, never replacements for exact facts. Continue
including raw facts in encrypted backups and complete private exports.

Use ordinary PostgreSQL tables and current owner/time/metric indexes. Do not add
TimescaleDB, native range partitioning, or cold storage now: the measured five-year
component is operationally modest, interactive reads are bounded, exports stream, and
dump/restore remains far inside the four-hour recovery objective.

Account and integration deletion continue to remove hot facts through audited paths.
Encrypted backup copies age out under backup retention. A private export made before
deletion is separately owner-scoped and expires after seven days. No archive may
silently outlive these rules.

There is no data migration. Existing rows remain authoritative in place. The isolated
restore PostgreSQL tmpfs grows from 2 GiB to 4 GiB because the measured five-year
relation alone approaches 2 GiB after a clean restore.

## Revisit thresholds

Open a measured ADR before changing this policy when any of these occurs:

- the Garmin metric relation reaches 10 GiB or ten years of retained data;
- a nightly dump exceeds 30 minutes or threatens local/offsite capacity;
- an isolated restore exceeds two hours, reducing margin inside the four-hour RTO;
- free database-host storage falls below the existing 25% operational threshold;
- exact selected-day reads or queued export/backup work fail documented budgets; or
- Garmin terms, privacy needs, or the owner's explicit preference changes.

Evaluate native PostgreSQL range partitioning first. Consider TimescaleDB only if
measured partitioning is insufficient and extension lifecycle, backup, correction,
deletion, export, and restore all pass. Consider encrypted lossless cold storage only
with retrieval, key recovery, checksum, deletion, export, and isolated-restore tests;
never substitute lossy summaries for facts.

## Verification

```bash
uv run python scripts/benchmark_wearable_backup_restore.py \
  --years 5 \
  --confirm CREATE-DISPOSABLE-SYNTHETIC-BACKUP-RESTORE \
  --output /tmp/healthcurve-wearable-retention.json
```

The runner starts two isolated PostgreSQL containers, migrates and seeds only the
source, dumps through the read-only backup role, restores into the second container,
verifies row count, whole-row signature, and sentinel, then destroys both. Its output
contains operational counts/timings only and must not be committed.
