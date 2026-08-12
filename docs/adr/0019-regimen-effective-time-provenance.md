# ADR-0019: Canonical regimen instants with preserved local-time provenance

Status: Accepted

## Context

Medication-plan effective dates were stored as timezone-less PostgreSQL timestamps.
The browser supplied `datetime-local` values, so a value such as 09:00 could be
interpreted as local wall time in the form but later compared as UTC. Assigning a
timezone to existing values after the fact would silently change historical meaning.
Daylight-saving gaps and repeated hours also make some local timestamps invalid or
ambiguous.

## Decision

New and edited regimen versions resolve effective wall times through the same IANA
timezone resolver used for recorded events. The API accepts an explicit
`effective_timezone` and otherwise derives the authenticated owner's configured IANA
timezone. A repeated hour requires an explicit fold; a skipped hour is rejected.

The existing `effective_from`, `effective_to`, and half-open `effective_period` remain
canonical UTC instants stored without a timezone solely for compatibility with the
PostgreSQL `tsrange` exclusion constraint. Each new write also stores the original
naive local wall time, IANA timezone, and capture-time UTC offset. Those provenance
fields are what the UI uses for human-readable plan dates.

The migration does not guess historical context. Existing rows retain their canonical
values and receive `legacy_naive_utc_ambiguous` provenance with null local timezone and
offset fields. The UI discloses that their original timezone is unknown.

## Consequences

- Active-plan selection and overlap exclusion continue to compare canonical instants.
- New effective dates survive travel, DST, and later timezone-database changes without
  losing the wall-clock meaning entered by the owner.
- A DST fold may require the caller to submit `effective_from_fold` or
  `effective_to_fold`; HealthCurve refuses to guess.
- Legacy effective dates remain queryable but cannot be presented as having recovered
  local-time provenance.
- API clients gain additive timezone, fold, local-time, offset, and provenance fields.

## Alternatives considered

- Treat every `datetime-local` value as UTC. Rejected because that changes the user's
  intended wall time outside UTC.
- Backfill all historical rows with the owner's current timezone. Rejected because the
  owner may have traveled or changed settings, and the original zone was never stored.
- Replace `tsrange` with timezone-aware range storage immediately. Rejected because the
  canonical UTC representation is already deterministic; a range-type rewrite adds
  migration risk without improving preserved provenance.
