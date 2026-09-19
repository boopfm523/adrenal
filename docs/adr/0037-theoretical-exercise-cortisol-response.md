# ADR-0037: Theoretical healthy cortisol response to measured exercise load

**Status:** Accepted — 2026-09-19. Amends ADR-0015 §2–§3 for this one versioned layer.

## Context

The owner asked to see how physical load — a long run, a day of yardwork — would have
changed a healthy person's cortisol, next to the modeled curve from actual doses and
the wake-anchored healthy P5–P95 reference (ADR-0026). The owner explicitly wants
theoretical context and no dose recommendations.

ADR-0015 §2 kept wearable context from becoming cortisol "demand", and §3 gated any
clinical-unit demand model behind a new ADR. The evidence for converting a consumer
stress score or symptoms into cortisol is still absent, and that boundary stands. The
evidence for exercise is different: controlled studies show a reproducible,
intensity-dependent rise in circulating cortisol in healthy adults once exercise passes
roughly 60% of aerobic capacity, and percent heart-rate reserve tracks percent of
aerobic capacity reserve closely enough to estimate intensity from Garmin heart rate.

## Decision

### 1. Intended use and interpretation boundary

`hc-exercise-response-v1` estimates how the healthy-adult reference band would move
under the owner's measured heart-rate load. It answers "what would a healthy adrenal
response look like during and after this activity?" It does not estimate the owner's
cortisol, requirement, deficit, or replacement dose, never changes the modeled dose
curve, and never produces alerts. It is labeled theoretical wherever it appears.

### 2. Model

- Intensity each minute: `I = clamp((HR − HR_rest) / (HR_max − HR_rest), 0, 1.2)` from
  Garmin intraday heart-rate samples. A sample covers until the next one only when the
  gap is within twice its cadence; unobserved minutes contribute no load.
- `HR_rest` is Garmin's daily resting heart rate for the date, else the median of the
  prior 14 days. `HR_max` is the higher of the observed 180-day peak and
  `208 − 0.7 × age` (Tanaka et al. 2001), using the same age assumption the healthy
  reference already displays. Age and sex are never inferred from records.
- Healthy response, as a fraction `E(t)` above the circadian total-cortisol level:
  `dE/dt = s(I) − k·E`, with `k = ln 2 / 75 min` (cortisol plasma half-life of roughly
  60–90 minutes) and `s(I)` calibrated so that 30 minutes at constant intensity reaches
  `2.075 × max(0, I − 0.40)`: no rise at 40%, +41.5% at 60%, +83% at 80% (Hill et al.
  2008). `E` is capped at 2.0 (threefold), bounding multi-hour endurance scenarios.
  Load from the four hours before local midnight carries into the selected day.
- Raised band: each reference percentile's serum total is multiplied by `1 + E(t)` and
  converted back to free cortisol with the reference's own binding equation.

### 3. Presentation

One optional, default-off checkbox on the Daily review HealthCurve draws the raised
median area and the raised P5/P95 edges on the existing free-cortisol axis. One
collapsible section lists inputs, parameters, formulas, per-activity load, references,
and limits. No banner, alert, or notice box is added. Chart language continues to avoid
"required cortisol", "shortfall", "adequate coverage", and dose amounts. The layer is
private: the public static site's allow-list does not copy it.

### 4. Limits

Evidence comes from short, controlled bouts in healthy, mostly young adults;
extrapolation to long durations relies on first-order kinetics and the cap. Heat,
dehydration, fasting, illness, psychological stress, training status, sex, and
time-of-day differences in the exercise response are not modeled. Garmin stress is not
used. There is no validation against the owner's timed measurements, and no conversion
to a dose is provided or implied.

## Consequences

The owner can see theoretical exercise-related healthy responses beside the modeled
curve without HealthCurve claiming a personal requirement. Parameter changes require a
new model revision.

## Alternatives considered

**Use Garmin stress or Body Battery as a demand multiplier.** Rejected for the ADR-0015
reasons; neither is validated against cortisol.

**Convert the raised area into milligrams.** Rejected: absorption, timing, and clearance
vary too much, and dose guidance is out of scope (SAFE-17).
