# ADR-0018: Do not compare relative hydrocortisone exposure with population cortisol reference bands

**Status:** Accepted — 2026-08-11

## Context

The owner wants a shaded, time-varying background on the selected-day HealthCurve so
the modeled effect of recorded doses can be viewed against where cortisol would
normally fall for a 47-year-old male. A shaded region is visually useful only when the
line and region describe comparable quantities. Otherwise, apparent intersections and
"inside the range" positions imply physiological adequacy that the data do not
support.

The strongest accessible whole-day demographic reference is the CIRCORT meta-dataset:
104,623 salivary samples from 18,698 people in 15 field studies. Its published table
reports LC-MS/MS-calibrated **salivary cortisol** percentiles by age, sex, and elapsed
time after awakening. For males aged 41–50, with awakening fixed at 07:00, the
5th/50th/95th percentiles in nmol/L are:

| Hours after awakening | 1 | 3.5 | 6 | 8.5 | 11 | 13.5 | 16 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 5th percentile | 1.8 | 1.0 | 0.6 | 0.4 | 0.3 | 0.2 | 0.2 |
| Median | 6.3 | 3.4 | 2.0 | 1.3 | 0.9 | 0.7 | 0.6 |
| 95th percentile | 22.6 | 12.3 | 7.3 | 4.7 | 3.3 | 2.5 | 2.1 |

These are population salivary reference values, not personal replacement targets.
They are relative to awakening rather than simply clock time, include large
between-person and between-study variance, and do not model exogenous oral
hydrocortisone in adrenal insufficiency. CIRCORT also found season effects. Assay
choice matters: primary work comparing salivary cortisol methods found materially
different reference intervals between assay generations.

Serum/plasma references are a different specimen and binding context. Published
hydrocortisone pharmacokinetic studies measure total or free serum/plasma cortisol
with formulation-specific and person-specific behavior. Oral exposure can be less
than dose proportional; body weight, absorption, cortisol-binding globulin, albumin,
and endogenous production affect the concentration-time profile. The Endocrine
Society guideline recommends clinical monitoring of glucocorticoid replacement and
suggests against using hormonal monitoring to adjust it.

Primary evidence:

- [Miller et al., CIRCORT (2016)](https://pmc.ncbi.nlm.nih.gov/articles/PMC5108362/),
  including its [published reference table](https://discovery.ucl.ac.uk/1505982/2/Kumari_CIRCORT_Tables.pdf);
- [Gagnon et al. (2018)](https://pubmed.ncbi.nlm.nih.gov/29470960/), salivary
  cortisol/cortisone assay-specific reference intervals;
- [Johannsson et al. (2016)](https://pmc.ncbi.nlm.nih.gov/articles/PMC5065076/),
  dual-release hydrocortisone plasma pharmacokinetics and less-than-dose-proportional
  exposure;
- [Johnson et al. (2018)](https://pmc.ncbi.nlm.nih.gov/articles/PMC5963674/), total
  and free serum cortisol and salivary cortisone after oral hydrocortisone;
- [Röhr et al. (2022)](https://pmc.ncbi.nlm.nih.gov/articles/PMC9231005/), a
  mechanistic model incorporating cortisol protein binding; and
- [Endocrine Society primary adrenal-insufficiency guideline](https://academic.oup.com/jcem/article/101/2/364/2810222).

## Decision

### 1. Do not draw a population cortisol band behind `hc-exposure-v1`

`hc-exposure-v1` remains a theoretical oral hydrocortisone exposure index in REU under
ADR-0013. REU has no validated conversion to salivary, total-serum, free-serum, or
plasma cortisol concentration. The CIRCORT band is salivary cortisol in nmol/L.

HealthCurve therefore must not place CIRCORT or a serum reference interval on the same
quantitative axis as REU, normalize both to 0–100 and imply comparability, color the
REU curve as in/out of range, calculate time in range, or describe the gap as
shortfall, need, coverage, low cortisol, high cortisol, safe, or adequate.

Age 47 and male sex select a valid CIRCORT stratum but do not solve the specimen, assay,
unit, awakening-time, exogenous-dose, or individual-pharmacokinetic mismatch. Age and
sex must never be inferred from health records or silently persisted for this purpose.

### 2. Permit reference context only when it is visibly non-comparative

A future feature may show the published CIRCORT rhythm in a separate, explicitly
labeled educational panel. It must retain saliva nmol/L, 5th/50th/95th percentile
labels, the assumed or recorded awakening time, demographic stratum, assay calibration,
source/version, and population uncertainty. It must say that the panel is not the
owner's expected blood level and cannot assess replacement adequacy. It must not be
overlaid with REU or presented as a target for dosing.

### 3. Gate a comparable shaded band on a new validated concentration model

A band may share an axis with a modeled line only after a new ADR and versioned model
establish all of the following:

- the same analyte, specimen, assay calibration, and concentration unit for both;
- formulation- and route-specific oral hydrocortisone pharmacokinetics;
- explicit endogenous-production, body-weight, binding-protein, absorption, and
  relevant covariate assumptions;
- a stated intended use that excludes medication recommendations;
- uncertainty and sensitivity analysis rather than a single precise line;
- validation against repeated, timed owner measurements obtained through an
  appropriate clinical protocol; and
- clinician review of interpretation boundaries.

Unsupported or missing inputs suppress the band. They are never replaced with defaults
that make the display look personalized.

## Consequences

The current HealthCurve continues to compare actual dose timing, relative exposure,
symptoms, Garmin observations, and vital facts without pretending to measure cortisol.
The owner does not yet get a shaded "normal" region behind the blue REU line, because
that visual would be mathematically and clinically misleading.

The CIRCORT values remain useful evidence for a later educational rhythm panel, and
the exact prerequisites for a genuinely comparable concentration band are explicit.
Any future implementation must use a distinct model version and cannot silently
reinterpret historical REU curves.

## Alternatives considered

**Normalize the CIRCORT percentiles and REU line to 0–100.** Rejected. Normalization
would preserve visual shape but erase units and falsely imply that overlap measures
adequacy.

**Treat the 41–50 male CIRCORT band as a personal target.** Rejected. It is a broad
population saliva distribution conditioned on an assumed wake time, not a personal
replacement target.

**Use morning/evening serum laboratory ranges.** Rejected for the overlay. Two
time-point serum intervals do not provide a validated continuous curve, and REU still
is not serum cortisol.

**Fit a concentration model from dose history alone.** Rejected. Dose timestamps do
not identify individual absorption, clearance, binding, or concentration without
repeated comparable measurements.
