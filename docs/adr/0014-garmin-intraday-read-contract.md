# ADR-0014: Read-only Garmin intraday metric contract

**Status:** Accepted — 2026-08-11

## Context

ADR-0012 approved a narrow unofficial Garmin Connect integration but deferred
continuous heart rate, HRV, respiration, and timestamped stress. HealthCurve's central
selected-day review now needs those recorded observations beside the theoretical
steroid-exposure curve. Daily aggregates cannot establish within-day timing, and an
absent wearable sample must never be drawn as zero or silently interpolated.

The pinned `garminconnect==0.3.9` implementation exposes the approved read-only,
single-date methods. Structure-only probes from the isolated Garmin worker on
2026-08-11 and 2026-08-15 verified
their configured-account response contracts without emitting metric values, raw
timestamps, account identifiers, credentials, or payloads:

| Series | Approved method and selected field | Timestamp semantics | Observed cadence | Unit and missingness |
|---|---|---|---|---|
| Heart rate | `get_heart_rates`; `heartRateValues` | descriptor-indexed `[epoch_ms, value]`; epoch is a UTC instant | predominantly 2 minutes; gaps occur | bpm; null/invalid/out-of-range is missing |
| Stress | `get_stress_data`; `stressValuesArray` | descriptor-indexed `[epoch_ms, value]`; epoch is a UTC instant | 3 minutes in the observed day | Garmin score, 0–100; negative sentinels are missing; zero remains a valid recorded score |
| Respiration | `get_respiration_data`; `respirationValuesArray` | descriptor-indexed `[epoch_ms, value]`; epoch is a UTC instant | 2 minutes in the observed day | breaths/min; negative sentinels are missing |
| HRV | `get_hrv_data`; `hrvReadings` | `readingTimeGMT` is the UTC instant; `readingTimeLocal` is provider context only | 5 minutes during the observed nightly interval | ms; absent/null response or invalid reading is missing |
| Steps | `get_steps_data`; `startGMT`, `endGMT`, and `steps` | explicit UTC interval bounds | 15-minute provider buckets in the observed day, deterministically summed into local-clock hours | steps; an observed zero remains zero and absent buckets remain missing |

The payloads also contain daily or nightly aggregate fields. In particular, HRV has
`lastNightAvg` and `lastNight5MinHigh`; respiration has waking/sleep averages and daily
high/low fields. Those aggregates are not substitutes for samples and remain separate
facts when implemented. The probe found no upstream per-sample ID, revision token, or
per-sample IANA timezone in any of the four contracts. Cadence is empirical device
behavior, not a provider guarantee.

The private endpoints have no published retention, quota, schema, or service-level
contract. Each approved client method accepts one calendar date. HealthCurve therefore
cannot promise historical availability. Its existing operational bounds remain a
maximum 31-day job window, at least 0.25 seconds between provider reads, bounded
retries/backoff, and re-reading recent dates to discover late provider changes.

## Decision

1. Expand the narrow adapter allow-list to exactly `get_heart_rates`,
   `get_stress_data`, `get_respiration_data`, `get_hrv_data`, and `get_steps_data`, in
   addition to the three reads approved by ADR-0012. No general Garmin client escapes
   the adapter and no mutating method is permitted.
2. Select only the series and aggregate fields named above. Raw responses remain
   in-memory, untrusted, bounded to 10,000 samples per series, and are discarded after
   deterministic mapping. CI and committed fixtures remain synthetic.
3. Resolve descriptor indexes by their semantic keys instead of assuming array column
   order. Treat malformed rows, nulls, negative sentinels, non-finite values, and
   out-of-range values as missing. A stress value of zero is valid and must not be
   mistaken for missingness.
4. Store UTC sample instants from epoch/GMT fields. Derive experienced display time
   from the owner-selected IANA timezone and retain offset. Because Garmin supplies no
   per-sample IANA zone, historical travel can require owner correction; the raw
   `readingTimeLocal` string is not sufficient to invent a zone.
5. Derive stable provider identity from normalized metric type plus UTC timestamp.
   Derive a revision hash from metric type, timestamp, normalized decimal value, and
   unit. Re-reading the same content is idempotent; changed content at the same identity
   creates a correction. Duplicate rows for one metric/timestamp are ambiguous and
   produce a warning rather than two indistinguishable facts.
6. Preserve source cadence. The UI may connect available points for readability only
   when it labels that visualization behavior; it must expose the exact-value table,
   sample count, and gaps. It must not create interpolated facts or turn a missing
   interval into zero.
7. Keep aggregate facts and intraday samples distinct. A daily/nightly average is
   labeled as an aggregate with its period; it is never plotted as though sampled at
   midnight. Intraday HRV readings and the provider's nightly HRV average are different
   facts even when calculated from related source data.
8. Project observed step buckets into one sample per local-clock hour by summing only
   valid provider intervals whose local start belongs to the selected day. This is an
   aggregation of observed facts, not interpolation: missing buckets are not invented,
   and an observed zero-step bucket remains part of the hourly total.
9. Retain every security, isolation, disconnect/deletion, export, correction, fallback,
   and failure boundary from ADR-0012. This ADR supersedes ADR-0012's method allow-list
   and initial metric contract, not those protections.

## Consequences

The selected-day review can use real timestamped wearable observations at their native
cadence and can visibly distinguish samples, aggregate summaries, and gaps. The
deterministic identity/revision contract supports overlapping re-reads without silent
rewrites despite the provider's lack of sample IDs.

HealthCurve remains dependent on undocumented private endpoints. Fields, cadence,
retention, authentication, and availability can change without notice. Capability
status and warnings must therefore be visible, and reviewed exports remain the durable
fallback. No wearable series is treated as diagnosis, causal evidence, cortisol
measurement, or dosing guidance.

## Alternatives considered

**Use daily aggregates as chart points at midnight.** Rejected because it fabricates
timing and cannot support within-day comparison with doses or symptoms.

**Persist the complete provider responses for future parsing.** Rejected because it
collects substantially more sensitive data than required and makes schema drift a
stored-data problem.

**Infer missing samples or convert Garmin stress sentinels to zero.** Rejected because
missingness and a measured zero have different meanings.

**Wait for an official Garmin API.** Retained as a future migration path, but it does
not satisfy the configured single-owner integration today. The export fallback remains
available.
