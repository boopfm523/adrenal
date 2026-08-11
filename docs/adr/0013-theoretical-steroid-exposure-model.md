# ADR-0013: Versioned theoretical steroid-exposure model from recorded doses

**Status:** Accepted — 2026-08-11

## Context

The central HealthCurve analytics workflow needs a selected-day curve that can be
viewed beside symptoms and recorded biometrics. The owner referenced the
[Clearly Alive theoretical steroid curve plotter](https://clearlyaliveart.com/theoretical-steroid-curve-plotter/),
which illustrates a rise after an entered dose followed by exponential decay. In
HealthCurve the dose grid is inappropriate: the authoritative inputs are the current,
correction-aware `DoseEvent` facts already shown on Timeline.

The administration timestamp and the modeled peak are not the same event. Published
single-dose studies of conventional oral hydrocortisone report rapid absorption but a
later and variable peak. Derendorf et al. observed a peak near one hour, 96% mean oral
bioavailability, and a 1.7-hour elimination half-life after 20 mg hydrocortisone.
Johnson et al. reported median total-serum-cortisol `Tmax` 1.125 hours with a 0.5–1.5
hour range. A randomized crossover study in people with secondary adrenal
insufficiency found that a one-compartment model described its data better than a
two-compartment model, while exposure varied more than tenfold between individuals and
total cortisol was not dose-proportional. More recent mechanistic models include
saturable protein binding and more complex absorption compartments.

Primary evidence and registration:

- [Derendorf et al., 1991](https://doi.org/10.1002/j.1552-4604.1991.tb01906.x),
  conventional oral and intravenous hydrocortisone pharmacokinetics;
- [Johnson et al., 2018](https://pmc.ncbi.nlm.nih.gov/articles/PMC5963674/), oral
  bioavailability and measured total/free cortisol and salivary cortisone;
- [Werumeus Buning et al., 2017](https://doi.org/10.1016/j.metabol.2017.02.005),
  randomized crossover population pharmacokinetics in secondary adrenal insufficiency;
- [SUPREME CORT, NCT01546922](https://clinicaltrials.gov/study/NCT01546922), the
  registered study underlying the population analysis;
- [Röhr et al., 2022](https://pmc.ncbi.nlm.nih.gov/articles/PMC9231005/), a
  one-compartment model that explicitly incorporates hydrocortisone protein binding.

The [Endocrine Society primary adrenal-insufficiency guideline](https://doi.org/10.1210/jc.2015-1710)
describes hydrocortisone's plasma half-life as roughly 90 minutes. The newer
[joint ESE/Endocrine Society guideline](https://doi.org/10.1210/clinem/dgae250)
distinguishes a 90–120 minute plasma half-life from an 8–12 hour biological half-life.
The reference plotter's use of “biological half-life” for a roughly 1.5-hour decay must
therefore not be copied into HealthCurve's labeling. This curve represents a simplified
plasma-exposure shape, not duration of all biological effects.

## Decision

### 1. Model a relative exposure index, not cortisol concentration or effect

Version `hc-exposure-v1` produces **theoretical hydrocortisone exposure index (REU)**.
REU means “relative exposure unit”; it has no conversion to nmol/L, µg/dL, receptor
occupancy, symptom protection, or clinical adequacy. A supported 1 mg dose has a
normalized peak contribution of 1 REU. This normalization makes overlapping dose
shapes readable; it is not a claim of linear measured cortisol pharmacokinetics.

Required user-facing language is:

> Theoretical hydrocortisone exposure—not a cortisol measurement or dosing guide.
> Absorption and clearance vary substantially between people and circumstances.

Charts, tables, exports, APIs, and reports must use `theoretical_exposure_reu` and the
model version. They must never label the series “cortisol level,” “blood cortisol,”
“coverage,” “safe,” “low cortisol,” or “high cortisol.” The model cannot recommend,
compare, reschedule, or create doses and cannot change a recorded fact or approved
plan.

### 2. Initially support one explicit medication/formulation/route tuple

The v1 parameter registry supports only:

| Parameter | v1 value | Unit / provenance |
|---|---:|---|
| normalized medication | `hydrocortisone` | exact curated medication identity |
| formulation | conventional immediate-release tablet | curated formulation class |
| route | oral | recorded `DoseEvent.route` |
| amount unit | mg | recorded decimal dose unit |
| absorption rate `ka` | 2.000000 | h⁻¹, chosen so the one-compartment curve peaks at about 1 hour |
| elimination half-life | 1.700000 | hours, Derendorf et al. 1991 |
| elimination rate `ke` | `ln(2) / 1.7` | h⁻¹, derived |
| peak time | 0.998758 | hours after administration, derived from `ka` and `ke` |
| peak normalization | 1 | REU per mg at the modeled peak, product definition |
| contribution horizon | 24 | hours after administration; residual is about 0.011% of peak |
| output sampling | 5 | elapsed minutes, plus exact administration and peak knots |

`ka = 2 h⁻¹` is a transparent approximation selected against the approximately
one-hour peak in Derendorf et al.; it is not a fitted owner-specific absorption
constant. All stored decimal parameters and derived values are versioned. A future
parameter change creates a new model version and never silently changes a frozen
analysis/report snapshot.

Modified-/dual-/delayed-release hydrocortisone, granules, liquids, compounded products,
hydrocortisone sodium succinate, injections, infusions/pumps, topical products,
prednisone/prednisolone, dexamethasone, and every other medication/route are unsupported
in v1. Medication display-name substring matching is forbidden. Unsupported doses
remain visible as recorded dose markers and in the exact-value table with a reason
code, but contribute no line and never default to hydrocortisone parameters.

Hydrocortisone-equivalent potency tables are not pharmacokinetic conversion tables.
Because v1 supports hydrocortisone only, the equivalence factor is exactly 1 mg HC per
mg and no other steroid is converted. Supporting another steroid requires a new
versioned tuple with primary evidence for its formulation-specific absorption and
elimination; therapeutic potency alone is insufficient.

### 3. Use a normalized one-compartment absorption/elimination curve

For elapsed time `t` in hours after the recorded administration instant:

```text
ke = ln(2) / elimination_half_life_hours
t_peak = ln(ka / ke) / (ka - ke)
raw(t) = exp(-ke*t) - exp(-ka*t)
shape(t) = raw(t) / raw(t_peak)
contribution(t) = amount_mg * shape(t) REU
```

Contribution is zero when `t < 0` or `t > 24 hours`. It begins at zero at the actual
administration instant, rises as the modeled absorbed amount enters circulation,
peaks at about one hour, and then decays. The implementation uses decimal inputs and a
specified IEEE-754 calculation path, rejects non-finite results, and rounds only API
display values—not intermediate sums.

The selected-day total is the pointwise sum of every supported current dose
contribution. A marker remains anchored at each actual administration instant, so the
chart never implies that the dose was taken at the modeled peak. Scheduled plan slots
and missed-dose absences are not inputs. Proximity is never a deduplication key: two
distinct current `DoseEvent` IDs one minute apart—or at the same recorded instant—each
retain a marker and contribute independently. Only correction-chain supersession can
remove an earlier fact from the sum.

### 4. Read current facts across the true local-day boundary

The request supplies a local calendar date and IANA timezone. Local midnight at the
start and following date are resolved separately, producing a 23-, 24-, or 25-hour UTC
interval across daylight-saving transitions. Calculations use UTC instants; labels use
the requested local timezone and include offset/fold where an hour repeats.

The query includes current dose facts from 24 elapsed hours before local-day start
through local-day end. This provides prior-day carryover without treating the previous
calendar day as a fixed 24 hours. No contribution is generated before its dose instant.
A dose exactly at day end belongs to the next local day. The exact day start is
inclusive and day end exclusive.

Corrections use the same correction-chain rule as Timeline: only the current leaf fact
contributes. If time, amount, unit, route, medication, or formulation is corrected, a
fresh calculation uses the corrected leaf and excludes its superseded ancestors. A
frozen report retains its original model version and source fact IDs/revisions rather
than silently changing.

Missing, invalid, ambiguous, or unsupported inputs never become zero exposure. The API
returns a structured exclusion with dose fact ID and a safe reason code such as
`unsupported_medication`, `unsupported_formulation`, `unsupported_route`,
`unsupported_unit`, or `invalid_parameter_set`. Raw notes and other health text do not
enter model diagnostics or logs.

### 5. Make deterministic gold cases part of the implementation contract

Implementations of `hc-exposure-v1` must pass these cases within absolute tolerance
`1e-6 REU` unless a case states otherwise:

1. **Before/at dose:** a 10 mg oral immediate-release hydrocortisone tablet contributes
   0 REU at -1 minute and at exactly 0 hours.
2. **Rise and peak:** the normalized shape is 0.559734294 at 0.25 h, 0.844986280 at
   0.5 h, and 0.999999371 at 1 h; the derived peak at 0.998757738 h is exactly 1 within
   floating-point tolerance. Multiply each value by the recorded mg amount.
3. **Decay:** the normalized shape is 0.800490809 at 2 h, 0.368824685 at 4 h,
   0.072319888 at 8 h, and 0.014156411 at 12 h.
4. **Overlap:** a 10 mg dose at 07:00 and 5 mg dose at 08:00 total exactly the sum of
   their independent contributions at every instant; at 08:00 the second contribution
   is zero and the first is approximately 9.999993714 REU. Repeat with doses one
   minute apart and with two distinct fact IDs at the same timestamp; neither may be
   dropped, merged, or allowed to reset the first curve.
5. **Carryover:** a supported dose at 23:30 on the prior local date contributes after
   selected-day midnight; a dose older than 24 elapsed hours does not.
6. **DST:** `America/New_York` spring-forward and fall-back dates contain 23 and 25
   elapsed hours respectively, with monotonically increasing UTC sample instants and
   unambiguous repeated-hour labels.
7. **Correction:** an original and its corrected leaf never double-count. Correcting
   the time moves both the marker and curve; correcting to an unsupported tuple leaves
   the marker and creates an exclusion.
8. **Unsupported input:** oral prednisone and intramuscular hydrocortisone sodium
   succinate produce no exposure contribution and a visible reason; they do not use a
   hydrocortisone-equivalent multiplier.
9. **Missingness and privacy:** no absent dose becomes a zero-dose fact, and model logs
   contain only opaque IDs, counts, model version, duration, and reason codes.

Property tests must additionally establish non-negativity over the supported horizon,
zero before administration, a single peak for one dose, linear addition of independent
contributions as a visualization operation, stable ordering for equal timestamps, and
deterministic results independent of database row order.

## Consequences

The owner gets a reproducible curve driven by what was actually recorded, with a
visible delay between taking a tablet and the modeled peak. Overlapping doses and
prior-day residuals are handled consistently, and historical snapshots remain
explainable by model version and source fact revisions.

The curve remains deliberately less complex than research PK models. It does not use
body weight, food, illness, gastrointestinal absorption, cortisol-binding globulin,
albumin, kidney/liver function, interacting medicines, assay values, or an individually
fitted clearance. Research reports large between-person variability and nonlinear
total-cortisol behavior, so HealthCurve must present the curve as a visualization aid
for comparing recorded timelines—not a patient-specific blood prediction.

V1's narrow support means unsupported steroid and emergency-injection records appear
as markers without a modeled line. That visible limitation is preferable to fabricated
parameters. Future support is additive through new, reviewed parameter sets and gold
cases.

## Alternatives considered

**Copy the reference plotter's linear rise and “biological half-life.”** Rejected
because the terminology conflates plasma elimination with longer biological effects,
and the fixed linear rise is not tied to the primary immediate-release evidence.

**Draw an instantaneous spike at administration.** Rejected because it erases the
owner's required distinction between taking a tablet and modeled bloodstream exposure.

**Predict serum cortisol in clinical units.** Rejected because dose alone cannot
support that claim; binding, endogenous cortisol, formulation, and individual PK vary
substantially. Such a feature would require measured samples and an independently
validated patient-specific clinical model.

**Convert prednisone and dexamethasone with potency ratios.** Rejected for v1 because
equivalent anti-inflammatory dose does not define formulation-specific absorption,
plasma kinetics, or biological-effect duration.

**Use a full transit-compartment/protein-binding model now.** Deferred. It is more
physiologically detailed but requires owner-specific covariates and validation that are
not available. The transparent normalized model better matches the current exploratory
analytics boundary.
