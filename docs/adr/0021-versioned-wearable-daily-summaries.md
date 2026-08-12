# ADR-0021: Versioned wearable daily summaries for bounded longitudinal reads

**Status:** Accepted — 2026-08-12

## Context

Five years at Garmin's observed intraday cadences produces about 3.68 million raw
provider samples. Selected-day HealthCurve reads remain fast and need every exact
sample, but a 366-day analytics or report request must not materialize hundreds of
thousands of ORM objects. Raw samples are immutable recorded facts with correction
history; a performance projection must not replace or masquerade as those facts.

Daily summaries also need honest missingness. A day without samples is not zero, and
provider cadence describes only observed intervals rather than an expected sample
schedule. Local days can contain 23, 24, or 25 elapsed hours across DST changes.

## Decision

1. Keep `fact.garmin_metric_event` as the authoritative source and keep selected-day
   charts and exact-value APIs on the bounded raw-sample path.
2. Store a rebuildable operational projection in `ops.wearable_daily_summary`, keyed
   by owner, local date, IANA timezone, metric, and summary version. It is neither a
   recorded fact, a physician-approved plan, nor AI-generated content. The AI role may
   read it but cannot insert, update, delete, or truncate it.
3. Version 1 preserves native unit, sample count, samples without cadence, unioned
   observed coverage, uncovered gap count and largest gap, nullable minimum/average/
   maximum, incompatible-unit state, and a SHA-256 source-revision watermark. A
   no-sample row has count zero and nullable values; zero is never fabricated.
4. Calculate coverage and gaps by clipping provider-cadence intervals to the actual
   UTC bounds of the requested local day. When samples have no cadence, coverage stays
   zero but gap count and largest gap stay unknown rather than invented.
5. Build absent summaries on demand in raw-read chunks of at most 31 local days.
   PostgreSQL upsert makes rebuilds idempotent. The backfill command commits each
   bounded chunk, so interruption is safely resumed by rerunning the same range.
6. Statement-level database triggers invalidate only affected date/metric summaries
   after Garmin sample inserts or physical deletes. Immutable corrections therefore
   remove both the replacement day and any superseded original day from the cache;
   the next read deterministically rebuilds them.
7. Longitudinal daily-pattern analytics and report wearable sections consume these
   summaries. Report snapshots freeze the summary version, values, and source
   watermark. Sparse sleep and activity events remain exact report facts.
8. Garmin disconnect-with-deletion removes summaries alongside raw facts. Account
   deletion remains protected by the owner foreign-key cascade.

## Consequences

Long-range memory use is proportional to days and metrics, while a cold-cache rebuild
never loads more than 31 days of dense samples at once. Multiple requested timezones
can create separate projections of the same facts; that is necessary because local-day
boundaries differ. Raw facts and their correction history remain available for exact
review and future summary versions.

The first request for an uncached range performs bounded writes to the operational
projection and can be slower than a warm read. Cache rows increase backup size slightly
but are independently rebuildable. A future summary formula creates a new version
rather than silently rewriting the meaning of frozen report inputs.

## Alternatives considered

**Replace raw samples with daily rows.** Rejected because it destroys selected-day
detail and correction provenance.

**Compute every long range from raw rows on every request.** Rejected because measured
multi-year volume makes synchronous ORM materialization unbounded.

**Treat absent intervals as zero or assume a fixed cadence.** Rejected because Garmin
does not guarantee cadence and missing observations are not measurements.

**Put summaries in the fact schema.** Rejected because deterministic derived values are
not recorded observations and must not blur the fact/plan/AI safety boundary.
