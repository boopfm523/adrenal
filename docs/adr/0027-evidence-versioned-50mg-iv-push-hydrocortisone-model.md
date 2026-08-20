# ADR-0027: Add an evidence-versioned 50 mg and 100 mg IV-push hydrocortisone model

**Status:** Accepted — 2026-08-20

## Context

HealthCurve's existing exposure models support recorded immediate-release oral
hydrocortisone. The owner is now recording actual inpatient administrations documented
by the hospital as **Hydrocortisone Inj Dose: 50 mg by intravenous push**. These are
recorded stress-dose facts, may occur without an approved outpatient plan, and must not
be sent through the oral absorption equation.

The published evidence is route- and dose-specific. Prete et al. fitted repeated 50 mg
intravenous hydrocortisone boluses in people with primary adrenal insufficiency using a
total-serum cortisol exponential with an initial increment of 1,347 nmol/L and an
elimination constant of 0.27 per hour. Jung et al. independently measured the high,
immediate total- and calculated-free-cortisol exposure after a 50 mg Solu-Cortef IV
bolus. The FDA label establishes that the 100 mg sodium-succinate presentation is
equivalent to 100 mg hydrocortisone and is intended for immediate intravenous
administration. A separate study administered 100 mg IV boluses but measured the
altered clearance of critical illness, so HealthCurve does not import that clearance
as a general personal curve. Older dose-ranging work also shows that IV hydrocortisone
pharmacokinetics vary with dose size.

The owner needs an exact recorded 100 mg emergency injection to influence the current
full model. V4.1 therefore adds a deliberately bounded, explicitly disclosed 2×
dose-proportional scenario relative to the 50 mg fit. It does not claim that the 100 mg
curve was separately fitted or that scaling is personalized.

This is an exploratory population model. It is not a personal concentration
measurement, receptor-effect model, medication-adequacy test, or dosing guide.

## Decision

### 1. Add a separate selectable model

Add `hc-mixed-route-free-v4`. It keeps the complete `hc-wake-free-v3` oral calculation
unchanged and adds a dedicated IV-push path. V1, v2, and v3 remain separately
selectable and behaviorally unchanged.

For each supported 50 mg or 100 mg IV-push fact, elapsed real hours `tau >= 0`
contribute the following increment in **total serum cortisol**:

```text
iv_total(dose, tau) = 1347 * (dose_mg / 50) * exp(-0.27 * tau) nmol/L
half_life = ln(2) / 0.27 = 2.567 hours
```

The `dose_mg / 50` term is exactly 1 for the fitted 50 mg reference and exactly 2 for
the bounded 100 mg scenario. It is not a general per-mg model and is not applied to
any other amount.

Every supported oral and IV contribution is evaluated from its actual recorded
administration instant. Repeated or closely spaced IV doses sum; there is no dependence
on a medication plan. IV contributions stop after the explicitly versioned 24-hour
model horizon.

The v3 oral free-cortisol contribution is first converted to total cortisol using the
existing nonlinear CBG/albumin binding equation. The IV total contributions are then
summed with that total and the result is converted back to free cortisol. This avoids
pretending that total and free cortisol have a constant ratio. Regular and stress
contributions remain separately attributable and sum exactly to the displayed result.

### 2. Bound support to the documented fact

The injectable path supports only a current, confirmed recorded fact with all of:

- medication `Hydrocortisone Inj Dose` (the legacy normalized synonym
  `Hydrocortisone sodium succinate` is accepted for historical visibility);
- formulation `intravenous push` or an explicitly recognized IV-injection synonym;
- route `intravenous`;
- amount exactly `50 mg` or `100 mg`.

Other amounts, routes such as intramuscular, missing formulations, other units, and
other medications remain recorded markers with an explicit exclusion reason. They are
not silently discarded, treated as zero, coerced to IV push, or extrapolated from the
50 mg fit. The 100 mg contribution is explicitly labeled as a 2× scenario rather than
a separately fitted curve. Supporting another dose or route requires new evidence and
a new model revision or superseding ADR.

### 3. Preserve recorded-fact provenance

Existing facts whose hospital record establishes the corrected medication name and IV
route are superseded through HealthCurve's correction service. The old row remains in
revision history, while the corrected current fact identifies `Hydrocortisone Inj
Dose`, formulation `intravenous push`, and route `intravenous`. No approved medication
plan is created or inferred.

### 4. Present limits and evidence in the product

The selector, chart legend, tooltip, exact-value alternative, and methodology panel
identify v4 as a full oral plus exact 50 mg/100 mg IV-push population model. The UI
publishes the equation, fitted half-life, supported boundary, 100 mg scaling
assumption, references, and an explicit note that it cannot advise dosing or establish
the owner's measured cortisol concentration.

Primary evidence:

- Prete et al., *JCEM* 2020, repeated IV hydrocortisone pharmacokinetics in primary
  adrenal insufficiency: <https://pmc.ncbi.nlm.nih.gov/articles/PMC7241266/>
- Jung et al., 2014, measured total and calculated free cortisol after 50 mg IV
  hydrocortisone: <https://pmc.ncbi.nlm.nih.gov/articles/PMC4280712/>
- FDA Solu-Cortef prescribing information (2024):
  <https://www.accessdata.fda.gov/drugsatfda_docs/label/2024/009866s121lbl.pdf>
- Boonen et al., 2015, 100 mg IV hydrocortisone clearance in critical illness (used to
  document the dose and the limits of importing a critical-illness fit):
  <https://pmc.ncbi.nlm.nih.gov/articles/PMC4413428/>
- Toothaker et al., dose-size effects after IV hydrocortisone sodium succinate:
  <https://pubmed.ncbi.nlm.nih.gov/7120045/>

## Consequences

- The owner's recorded 50 mg and 100 mg IV-push stress administrations appear in v4
  without an approved plan and influence the curve from their actual times.
- Oral results are unchanged in every existing model and in v4's oral component.
- The implied fitted elimination half-life is about 2.57 hours, but this is a
  population parameter and not a claim about the owner's individual clearance.
- V4 can represent repeated 50 mg/100 mg inpatient boluses, but it does not model
  infusion, intramuscular absorption, receptor effect, clinical response, or other
  dose sizes. The 100 mg curve is a bounded 2× scenario, not a separately fitted
  concentration curve.
- Corrected facts remain auditable and the old intramuscular entries remain available
  in revision history.

## Alternatives considered

- **Relabel the old entries only in the UI.** Rejected because it would conceal an
  incorrect route in the canonical recorded fact and break provenance.
- **Reuse the oral Bateman curve.** Rejected because IV push has no oral absorption
  phase.
- **Apply a linear per-mg multiplier to any IV dose.** Rejected because the cited
  dose-ranging evidence does not justify an unbounded simplification. V4.1 accepts
  only the exact 100 mg fact as a disclosed 2× scenario while excluding every other
  amount.
- **Replace v3.** Rejected because the owner explicitly needs the existing models
  preserved for comparison and rollback.
