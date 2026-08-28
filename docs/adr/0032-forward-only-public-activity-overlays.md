# ADR-0032: Forward-only recorded activity overlays

**Status:** Accepted — 2026-08-28

## Context

ADR-0029 established the one-way public static HealthCurve and its explicit public
allow-list. Garmin activity facts are already imported privately with start/end time,
activity type, duration, optional distance, and provider provenance, but the
selected-day dynamic loader and public exporter discard those records before the
shared chart receives them.

The owner wants walking, running (including indoor or treadmill), and rowing-machine
activity intervals overlaid on the private curve beginning today and on the public
static curve when today's completed day becomes eligible tomorrow. Earlier public
days do not need activity backfill. An activity is temporal context, not a numeric
measurement and not an input to the deterministic cortisol model.

## Decision

Amend ADR-0029 to extend its public allow-list with these recorded Garmin
activity fields:

- reviewed activity type limited to walking, running/treadmill, and indoor rowing;
- start and end instants, timezone/local display time, and duration;
- optional distance in miles; and
- the already public generic provider and confirmation labels.

Activity titles, provider/source identifiers, unrestricted location text, and all
other activity metadata remain excluded. Public activity projection starts with the
2026-08-28 local day. Existing static days before that date are not backfilled. The
normal complete-day, post-cutoff Garmin-sync, schema validation, privacy validation,
atomic bundle, and write-only deployment gates from ADR-0029 remain unchanged.

The shared chart renders each activity as a factual interval on the time axis with an
accessible exact-values list. Activity intervals remain visually and semantically
separate from modeled cortisol, doses, symptoms, and physician-approved plans. They
do not alter the curve, infer cortisol demand, establish causation, or produce dosing
guidance.

## Consequences

- The private selected-day curve can show today's supported imported workouts.
- The public site can show the same minimized activity context beginning when the
  2026-08-28 day passes its existing publication gate.
- Earlier public data files remain unchanged, avoiding a historical republish.
- Unsupported activities stay private and absent rather than being generalized.
- Missing activity facts remain missing and are never converted to zero or inferred
  from steps or heart rate.

## Alternatives considered

**Publish every Garmin activity.** Rejected because the owner requested only a small
set and data minimization is part of the public boundary.

**Publish activity titles.** Rejected because titles can contain locations or other
unrestricted text and are unnecessary for the chart.

**Backfill all previously published dates.** Rejected because the owner explicitly
does not need historical activity overlays.

**Convert activity into a numeric curve lane.** Rejected because activity is an
interval fact, not a measurement on a comparable scale.
