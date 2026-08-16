# ADR-0026: Add a wake-anchored free-cortisol reference and observed meal context

**Status:** Accepted — 2026-08-16

## Context

HealthCurve currently offers two stable, selectable representations of recorded oral
hydrocortisone doses:

- `hc-exposure-v1`, the relative-exposure model governed by ADR-0013; and
- `hc-physiology-v2`, the population-parameter plasma-free-cortisol scenario and
  optional clock-anchored illustrative band governed by ADR-0024.

The owner supplied a deterministic wake- and sleep-anchored healthy cortisol reference
module, rationale, and regression CSV. The module keeps serum free cortisol, serum
total cortisol, and salivary cortisol distinct; models nonlinear CBG/albumin binding;
anchors the cortisol-awakening response and daytime decline to observed wake time; and
anchors the overnight segment to observed sleep onset. It can also place
population-observed breakfast, lunch, and dinner pulses at recorded meal times.

The owner wants this reference and a corresponding hydrocortisone curve as a third
selectable model without replacing either existing model or band. The owner also made
three presentation decisions required before implementation:

1. use the broad P5–P95 population reference band;
2. retain the existing custom HealthCurve chart and its current behavior rather than
   rebuilding it with another chart library; and
3. use effortless, observed meal logging to drive meal pulses, with no invented meal
   time when no meal was recorded.

This decision amends the future-model gate in ADR-0018 for one explicitly illustrative
same-analyte population comparison. It does not change ADR-0018's prohibition against
overlaying a cortisol reference interval on the `hc-exposure-v1` REU axis or presenting
a population band as a personal dosing target. It is additive to ADR-0024; the v2 model
and `hc-circadian-context-v1` band remain unchanged.

## Decision

### 1. Add an independent third model and reference identity

The new selectable model is `hc-wake-free-v3`. Its primary modeled series is serum free
cortisol in nmol/L. The new reference identity is `hc-wake-reference-v1`, with P5, P50,
and P95 serum-free-cortisol series in the same unit.

Existing model identities, equations, defaults, payloads, and historical snapshots do
not change. An absent selector continues to use the established application default.
Selecting v3 is explicit and is preserved in the private URL, API response, export,
analysis fingerprint, tooltip, accessible table, and explanatory panel. An unknown
identity fails bounded validation rather than falling back silently.

Both v3 cortisol series share one absolute serum-free-cortisol axis. They are never
independently normalized. Total cortisol may be derived for a clearly labeled lab
comparison through the versioned nonlinear binding conversion, but it is not mixed
onto the primary free-cortisol axis. Salivary values remain a separate analyte/specimen
and are not plotted as though interchangeable.

The reference band is population context, not a personal target, normality judgment,
medication requirement, or measure of replacement adequacy. The UI may describe
whether modeled values are visually above or below the illustrative distribution, but
must not turn that position into a warning, reassurance, dose recommendation, or
emergency decision.

### 2. Integrate the supplied reference equations as a versioned deterministic service

`hc-wake-reference-v1` follows the supplied `cortisol_reference.py` equations and
published constants, protected by the supplied CSV and acceptance fixtures. The source
package is an implementation reference and is not copied wholesale into the repository
without review; HealthCurve's implementation owns explicit types, provenance, bounds,
and failure behavior.

For every selected local day, deterministic code regenerates the reference on the
actual 23-, 24-, or 25-hour timezone-aware interval. It uses:

- the current confirmed Garmin sleep session's observed wake and sleep-onset instants;
- confirmed observed meal events for that local day;
- the explicitly versioned population age/sex context selected for this model; and
- the versioned wake-amplitude-association flag and reference constants.

The default displayed ribbon is P5–P95 and the median is P50. P25–P75 may remain
available in the domain payload for research or a future display choice, but it is not
the default ribbon. The reference is generated on demand from current facts and is not
stored as a static daily truth. Frozen report or analysis artifacts retain model,
parameter, and source-data fingerprints so their historical result remains explainable.

If a trustworthy wake or sleep-onset bound is unavailable, HealthCurve does not invent
an observed fact. The API returns a typed assumption or exclusion, and the UI says
which fallback or omission applies. Missing wearable data remains missing, never zero.

### 3. Model hydrocortisone in free cortisol and preserve editable PK assumptions

`hc-wake-free-v3` models each supported immediate-release oral hydrocortisone dose in
serum free cortisol with concurrent absorption and first-order elimination. Current
confirmed dose facts, including prior-day carryover, contribute independently and sum.
Regular and stress-dose contributions remain separately attributable in tooltips and
tables while sharing the same pharmacokinetic equation.

The initial half-life, absorption duration or `tmax`, distribution volume, clearance,
and bioavailability values are versioned population defaults based on the supplied
`HC_PK_REFERENCE`. Owner edits create a new parameter revision rather than mutating an
old calculation. The model does not infer parameters from symptoms, Garmin metrics,
meal size, age, height, weight, or isolated laboratory values.

The nonlinear `total_from_free` and `free_from_total` conversion is versioned with the
model and tested as an invertible transformation over the supported range. Free
cortisol remains the calculation and symptom-correlation quantity; derived total
cortisol is display context for comparable serum laboratory results only.

### 4. Preserve the existing chart renderer and legacy interactions

The current custom SVG HealthCurve component remains the chart implementation. This
epic extends its typed data and rendering layers only where required for:

- a selectable v3 model;
- one absolute free-cortisol axis shared by the v3 model and v1 reference;
- independently showable P5–P95 fill and P50 line;
- existing hover, touch, keyboard, zoom, day-navigation, and series controls; and
- an accessible exact-value alternative.

There is no chart-library migration or wholesale renderer rewrite. Existing v1/v2
geometry, relative-display behavior, event markers, sleep lanes, tooltips, responsive
behavior, and accessibility contracts must remain behaviorally unchanged. Additive
v3 code is isolated behind the model selector so rollback means disabling or removing
the new model path, not reconstructing the legacy chart.

### 5. Record meals with one easy event and use only observed times

A meal is a recorded fact, separate from a reference curve, modeled concentration,
symptom, diary entry, dose, or physician-approved plan. Its minimum useful payload is:

- experienced local time, UTC instant, IANA timezone, and UTC offset;
- source, confirmation state, owner, capture time, and correction provenance; and
- optional size from `XS`, `S`, `M`, `L`, `XL`, or `XXL`.

Natural language such as “I had a meal, large” records a reviewable candidate at the
message's event time unless an explicit time is supplied. The web provides an equally
small “meal now” path. An omitted size remains unknown and is never defaulted to
medium. Corrections supersede rather than overwrite.

The supplied reference equations distinguish breakfast, lunch, and dinner pulses. If
the owner names the meal, that role is preserved. Otherwise HealthCurve assigns the
nearest of the versioned population breakfast/lunch/dinner reference anchors using the
observed local time, marks the role as deterministically inferred, exposes that
assumption, and permits correction. This role affects only the illustrative healthy
reference pulse.

Meal size is stored and displayed for later correlation, but it does not scale the
cortisol pulse in v1. No validated mapping from the six owner-friendly size labels to a
free-cortisol amplitude is supplied. Hydrocortisone absorption and PK are not changed
by a meal event in this revision.

When no confirmed meal is recorded, HealthCurve passes no meal pulse to the reference
engine. It does not use the module's decorative default clock times. The UI explicitly
states that meal-related reference pulses were omitted because meal times were not
recorded. Caffeine and detailed nutrition or macronutrient capture are out of scope.

### 6. Derive correlations without prescribing or alerting

Per-day deterministic features may compare the modeled free-cortisol curve with the
population reference distribution: time below P5 and P25, inter-dose trough depth and
time, maximum fall rate, symptom time since last dose, time and magnitude above P95,
and AUC comparison. Meal time and optional size may be retained as contextual
covariates.

These are descriptive research features. They do not establish causation, calculate a
required dose, trigger medication advice, or suppress symptom/emergency pathways. The
structural pre-wake gap between oral replacement and a healthy endogenous rise is
labeled expected for this model and excluded from urgent or prescriptive language.

## Consequences

HealthCurve gains a third, more detailed comparison whose modeled and reference
series use the same analyte and unit. Wake and sleep shifts no longer leave the
reference rhythm pinned to an arbitrary clock. Observed meals can add the population
reference's secondary pulses without forcing nutrition logging or fabricating meal
times.

The comparison remains a population scenario rather than a measurement or owner-
specific target. Its apparent precision is bounded by uncertain individual
absorption, clearance, binding proteins, illness effects, and the quality and
availability of wake, sleep, meal, and dose facts. P5–P95 is deliberately broad and
does not mean that every point inside it is safe or every point outside it is unsafe.

Retaining the custom renderer avoids a broad interaction and accessibility migration,
but requires disciplined isolation and regression testing as an additional absolute
axis and shaded band are introduced.

## Alternatives considered

**Replace v1 or v2.** Rejected. Existing models are useful historical comparison
tools, have published contracts, and must remain selectable and unchanged.

**Rebuild the chart with ECharts or Plotly.** Rejected for this epic. A mature chart
library could simplify axes, fill areas, and pan/zoom, but would risk regressions in the
current mobile, tooltip, event, sleep, accessibility, and exact-table behavior. The
current renderer can accept the additive model behind its existing component contract.

**Use P25–P75 as the default.** Rejected by owner choice. It is visually tighter but
would hide much of the documented population variability. P5–P95 is the selected
illustrative context.

**Use default breakfast, lunch, and dinner times when no meal is recorded.** Rejected.
Those pulses would look owner-specific while being decorative assumptions. Unknown
meal timing remains unknown.

**Scale meal pulses from XS–XXL.** Deferred. Size is convenient recorded context, but
the supplied reference does not validate an amplitude mapping for those labels.

**Model caffeine or detailed macronutrients.** Rejected for this scope by owner choice.
They would add capture burden and a new physiological contract unrelated to the
minimal meal-time requirement.

