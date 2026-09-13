# ADR-0035: Garmin sleep-session heart-rate samples

- Status: Accepted
- Date: 2026-09-13
- Amends: [ADR-0014](0014-garmin-intraday-read-contract.md)

## Context

ADR-0014 selected Garmin's per-day `heartRateValues` series. A read-only,
structure-only verification of the configured account found that this ordinary
series can stop at sleep onset. The following day's sleep response carries a separate
`sleepHeartRate` series covering the whole overnight session, including samples
whose actual local date is the prior day. The series overlaps the wake day's ordinary
series at identical two-minute timestamps and values. HealthCurve ignored it, so the
selected-day HealthCurve showed a false pre-midnight gap even though Garmin supplied
observations. No raw payloads or personal values were retained from that
verification.

## Decision

1. Select `sleepHeartRate` rows from the sleep response that HealthCurve already
   reads. The adapter allow-list and per-day provider read count do not change.
2. Accept only rows with an explicit `startGMT` UTC instant (epoch milliseconds or
   an ISO GMT string) and a bpm `value` from 1 to 260. Null, malformed, and
   out-of-range rows remain missing and produce
   `intraday_sleep_heart_rate_missing_or_invalid`. A non-list series produces
   `intraday_sleep_heart_rate_shape_invalid`. The raw response is discarded after
   mapping.
3. Store each sample as an ordinary heart-rate `provider_sample` at its actual
   instant, using the ADR-0014 identity (`heart_rate` plus UTC timestamp) and
   revision. Local time comes from the owner-selected IANA timezone, so a
   pre-midnight sample belongs to its real local day, not the provider day it was
   requested under. Provenance records `garmin_field_name=sleepHeartRate` and
   `garmin_source_member=sleep-heart-rate-sample`.
4. The ordinary `heartRateValues` series is authoritative at a shared instant:
   - Within one provider day, a sleep sample at an ordinary instant is dropped.
     An identical value yields one fact. A differing value keeps the ordinary
     reading and warns `intraday_heart_rate_sleep_conflict`. Observed cadence is
     calculated over the merged series.
   - Across days in one sync window, the same rule applies against every ordinary
     sample. Repeated sleep samples keep the earliest day and warn
     `intraday_sleep_heart_rate_duplicate_timestamp`.
   - Across sync runs, a sleep sample never corrects a current ordinary-series
     fact. An equal value is unchanged. A differing value is also left unchanged,
     and the run records the conflict warning. An ordinary sample can still correct
     a sleep-sourced fact through the normal provider-revision correction.
5. Capability status reports `intraday_sleep_heart_rate` separately from
   `intraday_heart_rate`.

## Consequences

Overnight heart rate is continuous across midnight when Garmin supplied it. The chart
still breaks at real cadence gaps because observed intervals are retained. Re-reading
the same responses is idempotent. A disagreement between Garmin's two series is
visible as a warning, never silently resolved in favor of the newer read. Wake-day
ordinary samples whose prior sample now comes from the sleep series can receive a
one-time provider correction for their observed interval. That correction preserves
history.

## Alternatives considered

**Treat conflicting shared instants as missing.** Rejected because it would remove
previously accepted ordinary readings and create new gaps.

**Prefer the most recent read.** Rejected because which series was read last depends
on the sync window. Two overlapping windows could then correct the same fact back and
forth.

**Keep sleep heart rate as a separate metric type.** Rejected because both series
measure the same quantity at the same cadence. Two current facts per instant would
double-count analytics and summaries.
