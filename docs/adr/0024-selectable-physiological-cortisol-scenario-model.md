# ADR-0024: Add a selectable physiological cortisol scenario model without implying dosing adequacy

**Status:** Accepted — 2026-08-15

## Context

The owner wants a second HealthCurve model that accounts for oral absorption before a
post-dose peak, concurrent absorption and elimination, carryover from earlier doses,
and a shaded circadian context region. The existing `hc-exposure-v1` curve is useful for
shape comparison but is a relative exposure unit (REU), not cortisol concentration.
It must remain available and historically stable.

The supplied model draft combines a Bateman oral-input equation, a post-hoc
cortisol-binding calculation, free-cortisol circadian anchors, stress multipliers, and
a coverage ratio. Those concepts cannot safely be presented as one validated clinical
model. In particular, population pharmacokinetics vary substantially, calculated free
cortisol can be biased, and no published source establishes a personalized continuous
"needed" range from age, sex, height, weight, Garmin readings, symptoms, or recorded
stress episodes.

The strongest directly applicable population pharmacokinetic study is Werumeus Buning
et al. It measured plasma free and total cortisol by LC-MS/MS in 46 people with
secondary adrenal insufficiency, fit one-compartment oral models, and found the
one-compartment model described the data better than the two-compartment model. Its
combined plasma-free-cortisol population means were clearance 235.78 L/h and volume of
distribution 474.38 L; the model fixed the oral absorption rate at 1.4/h and
bioavailability at 96%. Exposure varied by more than tenfold between participants.
Doubling dose was approximately dose proportional for free, but not total, cortisol.

Relevant primary evidence:

- [Werumeus Buning et al. (2017)](https://pubmed.ncbi.nlm.nih.gov/28521880/),
  DOI [10.1016/j.metabol.2017.02.005](https://doi.org/10.1016/j.metabol.2017.02.005),
  direct plasma-free and total oral-hydrocortisone population pharmacokinetics;
- [Simon et al. (2010)](https://pubmed.ncbi.nlm.nih.gov/20528006/), DOI
  [10.2165/11531290-000000000-00000](https://doi.org/10.2165/11531290-000000000-00000),
  one-compartment oral hydrocortisone pharmacokinetics in adrenal insufficiency;
- [Derendorf et al. (1991)](https://pubmed.ncbi.nlm.nih.gov/2050835/), DOI
  [10.1002/j.1552-4604.1991.tb01906.x](https://doi.org/10.1002/j.1552-4604.1991.tb01906.x),
  oral bioavailability and disposition;
- [Johnson et al. (2018)](https://pmc.ncbi.nlm.nih.gov/articles/PMC5963674/), direct
  total/free serum and salivary measurements after oral and intravenous hydrocortisone;
- [Dorin et al. (2009)](https://pubmed.ncbi.nlm.nih.gov/19026798/), DOI
  [10.1016/j.clinbiochem.2008.09.115](https://doi.org/10.1016/j.clinbiochem.2008.09.115),
  mass-action estimates of free cortisol from total cortisol and binding proteins;
- [Molenaar et al. (2015)](https://pubmed.ncbi.nlm.nih.gov/26169244/), showing that
  calculated free-cortisol equations can be too biased and imprecise in critical
  illness;
- [Debono et al. (2009)](https://pmc.ncbi.nlm.nih.gov/articles/PMC2684472/), DOI
  [10.1210/jc.2008-2380](https://doi.org/10.1210/jc.2008-2380), a 20-minute-sampled
  healthy total-serum cortisol rhythm in 33 people; and
- [Bornstein et al. (2016)](https://pubmed.ncbi.nlm.nih.gov/26760044/), DOI
  [10.1210/jc.2015-1710](https://doi.org/10.1210/jc.2015-1710), the Endocrine Society
  primary-adrenal-insufficiency guideline.

## Decision

### 1. Preserve v1 and add an explicit v2 model identity

`hc-exposure-v1` remains unchanged. The new model identity is
`hc-physiology-v2`. Model selection is an allowlisted request parameter and is included
in every response, export, persisted analysis fingerprint, tooltip, and explanatory
panel. The initial default remains `hc-exposure-v1`; choosing v2 never rewrites a v1
result or historical record.

The selector contract is:

| Request value | Primary modeled series | Unit |
|---|---|---|
| `hc-exposure-v1` | theoretical relative exposure | REU |
| `hc-physiology-v2` | modeled plasma-free-cortisol scenario | nmol/L |

An absent selector uses v1. An unknown selector returns a bounded validation error and
never silently falls back. The URL uses `model=<identity>` so day navigation, refresh,
and shared private links preserve the choice. API payloads expose `model_id`,
`model_revision`, `series_kind`, `unit`, parameter-source citations, and a source-data
revision fingerprint.

### 2. Use a direct plasma-free-cortisol oral model

V2 supports only immediate-release **oral hydrocortisone** dose facts. For dose `D_i`
in mg at elapsed time `t_i`, with `tau = t - t_i` in elapsed hours:

```text
ke = CL / V
Q_i = F * D_i / V * (1,000,000 / MW)

C_i(t) = 0                                                   when tau < 0
C_i(t) = Q_i * ka/(ka-ke) * (exp(-ke*tau)-exp(-ka*tau))     otherwise
C_free(t) = sum_i C_i(t)
```

`1,000,000 / MW` converts mg/L to nmol/L. Absorption and elimination operate
concurrently; elimination does not wait until a dose "hits the bloodstream."

Fixed population-mean parameters for revision `hc-physiology-v2.0.0` are:

| Parameter | Value | Unit | Basis |
|---|---:|---|---|
| `ka` | 1.4 | 1/h | fixed oral absorption rate in Werumeus Buning et al. |
| `F` | 0.96 | fraction | fixed oral bioavailability in that model |
| `CL` | 235.78 | L/h | population mean plasma-free-cortisol clearance |
| `V` | 474.38 | L | population mean plasma-free-cortisol distribution volume |
| `MW` | 362.46 | g/mol | cortisol molecular weight conversion constant |
| timestep | 5 | elapsed minutes | deterministic display grid |
| lookback | 48 | elapsed hours | prior-dose carryover window |

These give `ke = 0.4970276993/h`, elimination half-life `1.394584611 h`, and
`t_peak = ln(ka/ke)/(ka-ke) = 1.146858832 h` after an isolated dose. `F` is included
because `CL` and `V` in the selected direct-free model are not labeled apparent
`CL/F` and `V/F`. A future model using apparent parameters must not multiply by `F`
again.

This line is named **modeled plasma-free-cortisol scenario**, never measured cortisol,
the owner's cortisol level, replacement coverage, or medication need. V2 does not use
the supplied post-hoc CBG/albumin shortcut in its primary series because a direct free
model is available and the shortcut is not reliable across acute illness.

### 3. Define deterministic summation and edge behavior

- All supported current dose facts in the selected day plus the preceding 48 elapsed
  hours contribute. Corrected or superseded facts do not contribute.
- Simultaneous and closely spaced doses are independent inputs whose concentrations
  sum; there is no de-duplication by time and no artificial reset at midnight.
- Regular and stress-dose contributions are calculated separately and summed into the
  total. Classification changes provenance and tooltip decomposition, not PK.
- `tau` is elapsed real time between UTC instants. Local labels use the requested IANA
  timezone, so 23-hour and 25-hour DST days remain physically correct.
- If `abs(ka-ke) < 1e-6`, use the finite limit
  `Q_i * ke * tau * exp(-ke*tau)`.
- Exponentials are evaluated only for nonnegative `tau`; outputs are finite,
  nonnegative decimals. Values below `1e-9 nmol/L` may render as zero but retain their
  unrounded value in the deterministic payload.
- Missing amount, unit, route, formulation, or experienced time excludes that fact and
  returns a safe exclusion reason. Non-mg units are converted only through an existing
  explicit medication-unit conversion. No potency-equivalent inference is allowed.
- Non-oral routes, modified/dual-release hydrocortisone, pumps/infusions,
  prednisolone, dexamethasone, fludrocortisone, and emergency injections are unsupported
  by this revision and are never coerced into the curve.

### 4. Keep the shaded region explicitly illustrative

The optional shaded series is named **illustrative circadian context band**, not
normal range, desired range, need, target, safe range, or adequate coverage. It uses
free-cortisol nmol/L so its vertical scale is compatible with the v2 line, but its
initial anchors come from the owner-supplied synthetic modeling brief rather than a
validated demographic reference interval:

| Local clock time | Center (nmol/L) |
|---|---:|
| 00:00 | 2.2 |
| 03:00 | 6.5 |
| 06:00 | 20.0 |
| 07:30 | 22.0 |
| 09:00 | 17.0 |
| 12:00 | 12.0 |
| 16:00 | 7.5 |
| 20:00 | 4.0 |
| 23:00 | 2.6 |
| 24:00 | 2.2 |

Revision `hc-circadian-context-v1` defines lower and upper bounds as `0.8 * center`
and `1.2 * center`. PCHIP interpolation is used separately for center, lower, and
upper values without overshoot; all anchors are sampled exactly. The local civil-day
axis is constructed from timezone-aware instants. Repeated DST clock times remain
separate elapsed instants; nonexistent local times are skipped rather than invented.

This band is a product-specified educational scenario informed only in **shape and
phase** by healthy rhythm studies. It is not the Debono total-serum 95% reference
interval, is not specific to a 47-year-old man, and is not personalized by height or
weight. Debono's cohort included 24 men and 9 women aged 17–57 and reported no weight
or sex difference in that sample, but that does not establish an individual range.
CIRCORT salivary percentiles remain incompatible under ADR-0018.

The band is default-off until its limitations are visible in the same view. Users may
show or hide it independently with either v1 or v2 selected. With v2, the band and
modeled line share their nmol/L display domain. With v1, the REU line and nmol/L band
retain independent relative display domains; their vertical positions are illustrative
only and do not imply unit equivalence or medication adequacy. No time-in-range,
deficit, excess, coverage ratio, traffic-light color, or "you need" calculation is
produced.

### 5. Recorded stress remains context, not inferred physiology

Recorded stress episodes may be shown as event intervals and may later select an
explicit, versioned **synthetic scenario multiplier**. In v2.0.0 the multiplier is
fixed at neutral `1.0`: episodes do not move the band or alter absorption, clearance,
or elimination. The supplied 1.5–6 multipliers are not adopted because their severity
mapping is not validated for an individual.

Garmin stress, heart rate, HRV, respiration, symptoms, diary text, temperature, and
other observations never silently become cortisol demand. Missing severity remains
missing. This preserves ADR-0015.

### 6. Golden fixtures are part of the model contract

Implementations must match these double-precision values before display rounding.
Unless stated otherwise, absolute tolerance is `1e-6 nmol/L` and time tolerance is
`1e-9 h`:

| Fixture | Expected result |
|---|---:|
| isolated 1 mg, before dose | 0 nmol/L |
| isolated 1 mg at 0.25 h | 1.544866648 nmol/L |
| isolated 1 mg at 0.5 h | 2.453002599 nmol/L |
| isolated 1 mg at 1 h | 3.131366639 nmol/L |
| isolated 1 mg at 2 h | 2.677108979 nmol/L |
| isolated 1 mg at 4 h | 1.153520516 nmol/L |
| isolated 1 mg at 8 h | 0.162244712 nmol/L |
| isolated 1 mg at 12 h | 0.022235822 nmol/L |
| isolated 1 mg at 24 h | 0.000057120 nmol/L |
| isolated-dose peak time | 1.146858832 h |
| isolated 10 mg peak | 31.573881313 nmol/L |

The peak concentration fixture is evaluated at the exact peak time, not at the
five-minute grid. A simultaneous 10 mg regular plus 5 mg stress dose equals the sum of
their independently evaluated contributions at every time. Two otherwise identical
doses separated by one minute must produce two inputs and a continuous summed shoulder.
A corrected dose contributes only through the current correction-chain head. A prior
day's supported dose contributes according to elapsed UTC time. V1 fixtures and payloads
must remain byte-for-byte identical apart from additive selector metadata explicitly
introduced by the selector issue.

Reference-band fixtures use exact anchors; for example, at 07:30 the center/lower/upper
are `22/17.6/26.4 nmol/L`, and at 20:00 they are `4/3.2/4.8 nmol/L`. Interpolated
samples use absolute tolerance `1e-9 nmol/L` against the selected PCHIP implementation.

### 7. Safety language is asymmetric

The UI and reports may say the model or observations suggest a question worth review.
They must not say the modeled value is adequate, safe, sufficient, normal for the
owner, protective, or evidence that symptoms can be ignored. No green in-range state,
check mark, reassuring alert suppression, diagnosis, causal claim, or medication
recommendation is allowed. Symptom and emergency pathways operate independently and
cannot be downgraded by this model.

## Consequences

HealthCurve gains a more physiologically interpretable selectable scenario without
changing v1. The v2 peak is delayed because absorption and elimination happen
concurrently, and close doses naturally merge through summation. Direct-free model
parameters avoid an unnecessary binding approximation, but population variability and
the unvalidated context band remain prominent limitations.

The owner can compare timing and shape, not determine a personal dose requirement.
Meaningful personalization would require an appropriate clinician-designed series of
timed free-cortisol measurements and a separately accepted calibrated model. Total
serum, salivary, and free plasma values are not interchangeable.

## Alternatives considered

**Replace v1.** Rejected. It would reinterpret historical REU curves and remove a stable
comparison model.

**Delay elimination until absorption completes.** Rejected. First-order oral input and
elimination occur concurrently; the sequential mental model is not physiologically or
mathematically correct.

**Transform modeled total cortisol through fixed CBG and albumin values.** Rejected for
the primary v2 line because direct plasma-free-cortisol PK is available and calculated
free cortisol can be substantially biased during illness.

**Overlay the Debono total-serum or CIRCORT salivary reference bands.** Rejected. They
do not match the v2 plasma-free analyte/specimen contract.

**Treat age, sex, height, and weight as personalization.** Rejected. No validated
covariate equation supports that claim for this model.

**Implement stress multipliers and coverage ratios from the supplied draft.** Rejected
for v2.0.0. The proposed values are useful hypotheses but do not establish individual
physiological need or medication adequacy.
