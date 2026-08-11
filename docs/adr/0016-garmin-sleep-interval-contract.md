# ADR-0016: Explicit Garmin sleep intervals on the selected-day HealthCurve

**Status:** Accepted — 2026-08-11

## Context

HealthCurve already retained a Garmin sleep session's experienced start, end,
duration, aggregate awakening count, and optional score. It did not retain stage
timing, and the selected-day Garmin query discarded sleep rows before building the
main HealthCurve. An aggregate count cannot locate an awakening on the time axis, and
filtering only by session start omits an overnight session from its wake day.

Garmin Connect sleep responses may provide bounded `sleepLevels` entries with GMT
start/end instants and an activity level. Reviewed FIT files may instead provide a
sequence of explicit `sleep_level` transitions. These unofficial/private and export
contracts can be absent or change, so missing stage detail must remain missing.

## Decision

1. Keep `GarminSleepEvent` as the canonical immutable/correctable recorded fact for
   one sleep session. Store each explicitly bounded awake interval as a normalized
   child of that exact sleep revision. Correction history therefore also preserves
   the stage detail used by the prior revision.
2. Accept only finite, ordered stage bounds wholly inside the session. Retain awake
   intervals needed for the HealthCurve; do not retain or display unsupported detail
   for light, deep, REM, or unclassified stages. Reject overlapping awake intervals
   with a safe warning.
3. Map Garmin's explicit awake activity value or awake label. For FIT transitions,
   create an awake interval only when an awake transition has a later explicit
   transition that supplies its end. A terminal awake transition can define the
   session end but does not invent a positive-duration interval.
4. Never derive stage times from `awakeCount`, duration, sleep score, or population
   assumptions. The UI may state that awakenings were reported but exact timing is
   unavailable.
5. Add an owner-scoped, paginated one-day endpoint using interval overlap:
   `session_start < day_end` and `session_end > day_start`. UTC instants, original
   session time context, provider provenance, and DST-aware IANA day bounds remain
   authoritative.
6. Render sleep as context behind the existing relative curves: a session band,
   vertical sleep-start and wake/end lines when those instants fall inside the
   selected day, and a distinct translucent awake interval. Exact rows remain in the
   accessible table. Poll the selected-day query once per minute so completed
   background syncs appear without user data entry.
7. Include stage rows in complete export, disconnect impact counts, physical Garmin
   deletion, and account deletion. The child foreign key cascades only when its exact
   parent fact revision is physically deleted.

## Consequences

The main HealthCurve can align known sleep and wake timing with doses, symptoms, and
wearable observations without presenting an inferred sleep trace. Overnight sessions
are visible on their wake day, and explicit intermediate awake periods are visually
distinct. Some sessions will have only start/end markers because Garmin did not
provide usable stage intervals; this is correct missingness, not a sync failure.

The implementation adds one fact table and an API field. It deliberately stores less
stage detail than Garmin may expose. If future analysis needs REM, deep, or light
intervals, that expansion requires a separate Bead and review of its clinical meaning,
display density, provider mappings, and privacy value.

## Alternatives considered

**Evenly distribute `awakeCount` through the session.** Rejected because it fabricates
experienced times and could create false associations with a dose or symptom.

**Store the complete raw sleep response.** Rejected because it retains substantially
more sensitive provider data than the selected-day purpose requires and makes
untrusted schema drift persistent.

**Filter sleep by start date.** Rejected because normal overnight sessions would
disappear from the wake-day review.

**Plot every sleep stage as another numeric curve.** Rejected because stage categories
are not continuous measurements and would make the already dense HealthCurve harder
to interpret.
