# ADR-0034: Garmin nap events

- Status: Accepted
- Date: 2026-08-31

## Context

Garmin exposes owner-recorded naps separately from its overnight daily-sleep response. The pinned read-only client provides these records through the body-battery events endpoint. That response also contains body-battery and stress arrays and subjective feedback that HealthCurve does not need to visualize a nap.

Treating naps as ordinary overnight sleep would corrupt final-wake selection, wake-anchored reference inputs, and nightly sleep comparisons. Treating their absence as zero would also misrepresent provider missingness.

## Decision

- The read-only Garmin adapter may call the body-battery events endpoint and select only nested events whose `eventType` is `NAP`.
- HealthCurve retains only the nap start instant, provider duration, derived end instant, explicit `nap` kind, stable provider identity/revision, and ordinary import provenance. It discards feedback, body-battery impact, stress values, descriptor arrays, and the raw response.
- The UTC provider start is converted with the owner's configured IANA timezone. A provider offset alone is not promoted into an IANA timezone.
- Naps and overnight sessions share the immutable Garmin sleep fact table, distinguished by a required `sleep_kind`. Existing and file-imported sleep records are `overnight`.
- Multiple naps per day are supported and reconciled idempotently. Invalid or unavailable nap data remains missing and produces a privacy-safe warning code.
- Naps are shown distinctly on private and public daily curves, health-data views, timelines, chat context, and reports.
- Nap records are excluded from final-wake selection, wake-anchored reference inputs, nightly sleep baselines, and average-wake calculations.

## Consequences

The integration adds one bounded read call per synced day. Static publication exposes only the same coarse sleep interval fields already allowed for overnight sleep, plus the non-sensitive session kind. A future Garmin payload change fails closed without storing the untrusted response.
