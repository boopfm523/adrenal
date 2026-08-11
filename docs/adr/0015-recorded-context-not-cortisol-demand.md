# ADR-0015: Recorded context overlays without inferred cortisol demand

**Status:** Accepted — 2026-08-11

## Context

The defining HealthCurve review places the theoretical exposure curve from actual
doses beside symptoms, stress, wearable measurements, and vital signs. The owner also
provided an exploratory model and example that contrast a drug curve with a
stress-responsive cortisol requirement band. That separation is useful: a stressor
can change physiology without changing what medication entered the body. The supplied
implementation nevertheless depends on population cortisol targets, protein-binding
assumptions, fixed pharmacokinetic parameters, and adequacy thresholds that have not
been validated for this owner.

Primary evidence supports substantial changes in cortisol dynamics during major
stress, including reduced cortisol clearance in critical illness, but it does not
validate a universal minute-by-minute requirement curve derived from Garmin stress or
symptom scores. Cortisol-binding globulin, albumin, illness, oral absorption, and
individual pharmacokinetics can materially affect measured total and free cortisol.
HealthCurve does not currently have the repeated clinical samples or clinician-
approved target needed to identify those parameters.

The relevant evidence includes [Boonen et al.](https://pubmed.ncbi.nlm.nih.gov/23506003/)
on reduced cortisol metabolism in critical illness,
[Prete et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC7241266/) on cortisol delivery
during major stress, [Lewis and Elder](https://pmc.ncbi.nlm.nih.gov/articles/PMC3813945/)
on cortisol-binding globulin, and
[Röhr et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC9231005/) on mechanistic
protein-binding pharmacokinetics. None provides a validated conversion from the
consumer Garmin stress score or a subjective symptom severity to cortisol
concentration or required replacement dose.

## Decision

### 1. Version the first overlay as recorded context, not inferred demand

`hc-context-overlay-v1` is a presentation/query contract. It combines these separate
lanes for one selected local day:

| Lane | HealthCurve fact | Rendering contract |
|---|---|---|
| Theoretical exposure | Current actual `DoseEvent` facts through `hc-exposure-v1` | REU line and distinct administration markers |
| Symptoms | Current `SymptomEvent` facts | Discrete markers retaining name and original 0–10 severity |
| Stress episodes | Current interval facts with mild/moderate/severe classification | Labeled interval bands; ordinal categories are not converted to Garmin scores |
| Garmin stress | Timestamped provider samples, 0–100 | Its own native-scale series with explicit gaps |
| Heart rate | Garmin samples and recorded BP pulse | bpm series/points with provenance kept distinct |
| HRV | Garmin samples | ms series with explicit gaps |
| Respiration | Garmin samples | breaths/min series with explicit gaps |
| Blood pressure | Current recorded vital facts | Discrete systolic/diastolic points in mmHg |

The exposure trace is never raised, lowered, delayed, or otherwise mutated by any
context lane. Nearby and simultaneous dose contributions continue to add independently
under ADR-0013. Missing context observations remain gaps, never zeroes or fabricated
interpolated facts.

The UI may normalize a symptom marker's visual size or position as
`severity_fraction = severity / 10`, clamped only after validating that the stored
integer is in the existing 0–10 domain. It must retain and expose the original value.
This display normalization is not a demand formula and must not combine symptom values
across names into a clinical score.

### 2. Preserve unlike stress concepts instead of collapsing them

A Garmin stress sample, a user-recorded stress episode, a symptom, an illness note,
and a life event have different definitions and provenance. V1 displays each as
recorded context. It does not convert one to another or into cortisol concentration,
"demand," a dose multiplier, or a coverage ratio. Visual alignment on a shared time
axis means temporal comparison only; it does not imply correlation or causation.

The exact-value alternative must state each series' unit, source, sample count,
missingness, and definition. Differently scaled series require separate axes, lanes,
or normalization clearly labeled as display-only. Chart language must not use
"required cortisol," "shortfall," "adequate coverage," "safe," or equivalent clinical
judgments.

### 3. Gate any clinical-unit demand or coverage model as a new decision

A future model that estimates total/free cortisol, physiological requirement, or
exposure-to-demand coverage requires a new version and ADR. Before it can be presented
as anything beyond a synthetic scenario, it needs all of the following:

- a stated intended use and clinician-reviewed interpretation boundary;
- validated target data for the relevant population and stress states;
- dated owner-specific or justified population pharmacokinetic parameters;
- an explicit endogenous-production assumption;
- validated absorption handling for food, vomiting, diarrhea, illness, formulation,
  and route;
- justified cortisol-binding globulin/albumin handling when clinical units are used;
- uncertainty bounds and sensitivity analysis;
- validation against repeated timed clinical measurements; and
- independent safety thresholds that do not turn exploratory analytics into dosing
  advice or alerts.

Consumer wearable values and symptom scores may remain covariates or context, but they
cannot become medication-demand multipliers without direct validation.

### 4. Record current data gaps and additive enhancement paths

HealthCurve can build v1 now from actual doses, symptoms, stress episodes, life events,
BP/pulse, weight, labs, and Garmin heart-rate/stress/HRV/respiration samples. It has
partial context for illness, activity, sleep, body temperature, salt craving, and
isolated cortisol/glucose labs.

Potential future collection is additive and optional: structured vomiting or
inability-to-retain-dose events, meals/caffeine/alcohol, continuous temperature, CGM,
SpO2, active energy/intensity, orthostatic BP pairs, sleep stages, CBG/albumin,
interaction flags, personal PK fitting, and pump/infusion schedules. Absence of these
streams is exposed as a model limitation; it is not imputed.

## Consequences

The first selected-day experience can be useful immediately and can faithfully show
whether symptoms and recorded biometrics occur near modeled exposure changes. It uses
the owner's actual timestamps and preserves dense wearable data without claiming that
those observations measure cortisol or dictate replacement need.

HealthCurve will not initially reproduce the supplied chart's clinical-unit
requirement band or adequate/shortfall ratio. That avoids false precision and leaves a
clean extension point for a future personally calibrated model. The owner may choose
which missing data streams are worth collecting after using the recorded-context view.

## Alternatives considered

**Apply the supplied requirement formula and thresholds directly.** Rejected because
its target anchors, binding assumptions, and coverage cutoffs are not validated for
this owner, and its timing jitter is inappropriate when HealthCurve has actual dose
times.

**Use Garmin stress as a direct cortisol-demand multiplier.** Rejected because the
consumer score is not a cortisol assay or validated replacement-requirement measure.

**Hide stress until a complete physiological model exists.** Rejected because recorded
context on a shared timeline is useful without a causal or dosing claim.

**Fit a personal clinical-unit model from current data.** Deferred because the project
has isolated labs rather than repeated timed cortisol, CBG/albumin, and absorption data
needed for validation.
