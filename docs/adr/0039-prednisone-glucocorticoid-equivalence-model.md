# ADR-0039: Combined hydrocortisone + prednisone glucocorticoid-equivalence model (v5)

**Status:** Accepted — 2026-11-15; default-selection portion of ADR-0028 superseded

## Context

The owner is starting a regimen that may alternate between oral hydrocortisone and
oral prednisone 5 mg daily, and wants the Daily Review curve to stay correct and
readable across that switch: whichever drug was taken, and when both are taken on
the same day (for example prednisone at wake and hydrocortisone in the afternoon),
the chart should show one theoretical curve with each drug's contribution visible
on hover.

ADR-0013 explicitly declined to support prednisone in `hc-exposure-v1`:

> **Convert prednisone and dexamethasone with potency ratios.** Rejected for v1
> because equivalent anti-inflammatory dose does not define formulation-specific
> absorption, plasma kinetics, or biological-effect duration.

ADR-0024 repeated the boundary for `hc-physiology-v2`: "prednisolone, dexamethasone,
fludrocortisone, and emergency injections are unsupported by this revision." Both
objections are correct and still apply to those frozen model versions: prednisone is
not cortisol, converting it mg-for-mg by therapeutic potency alone invents a plasma
curve with no formulation-specific evidence, and `hc-exposure-v1`/`hc-physiology-v2`
must stay byte-for-byte stable for historical snapshots.

This ADR does not reopen those models. It adds a **new**, separately versioned model,
`hc-full-glucocorticoid-v5`, that extends the unchanged `hc-mixed-route-free-v4`
hydrocortisone calculation (ADR-0024/ADR-0027/ADR-0028) with an independently sourced
prednisone contribution — satisfying ADR-0013's own stated bar: "Supporting another
steroid requires a new versioned tuple with primary evidence for its
formulation-specific absorption and elimination; therapeutic potency alone is
insufficient."

### Primary evidence

Prednisone is a prodrug rapidly and near-completely converted to active prednisolone.
Published oral pharmacokinetics:

- [DrugBank DB00635](https://go.drugbank.com/drugs/DB00635): immediate-release oral
  prednisone peaks (as prednisolone) at approximately 2 hours, elimination half-life
  approximately 2–3 hours.
- Rose JQ et al., *Bioavailability and pharmacokinetics of prednisone in humans*
  ([indexed record](https://katalog.lib.cas.cz/KNAV/EdsRecord/edsair,edsair.doi.dedup.....4cce0d4713e4ed67d99ae25538910bae)):
  dose-dependent oral prednisone / IV prednisolone kinetics in healthy adults.
- A nonlinear oral-prednisolone bioavailability study across 5–200 mg in 43 subjects
  ([indexed record](https://cir.nii.ac.jp/crid/1363951793207555456)) reports an oral
  bioavailability of 98.5% ± 4% and an elimination half-life of approximately
  3.5–3.7 hours, with the nonlinearity attributed to volume of distribution rather
  than half-life.

v5 uses the midpoint of the commonly cited peak-time range (1.5 hours) and the larger
study's half-life (3.5 hours) as transparent, literature-anchored timing constants —
the same "chosen against the primary evidence, not an owner-fitted constant" posture
ADR-0013 used for hydrocortisone's own `ka = 2 h⁻¹`.

Prednisone's amplitude is not expressed in a literal prednisolone assay unit. Instead
it reuses a long-standing clinical glucocorticoid dose-equivalence table (oral/IV;
mineralocorticoid activity and intramuscular/intra-articular potency are explicitly
out of scope): cortisone 25 mg = hydrocortisone 20 mg = prednisone/prednisolone 5 mg =
methylprednisolone 4 mg = dexamethasone 0.75 mg, giving prednisone/prednisolone
roughly 4× hydrocortisone's anti-inflammatory potency per mg. Sources:
[PMC10080509, Table 3](https://pmc.ncbi.nlm.nih.gov/articles/PMC10080509/table/Tab3)
and [LITFL — Steroid Conversion](https://litfl.com/steroid-conversion/).

## Decision

### 1. A new model identity; v1–v4 stay exactly as they are

`hc-full-glucocorticoid-v5` is a new, additive model. It does not change
`hc-exposure-v1`, `hc-physiology-v2`, `hc-wake-free-v3`, or `hc-mixed-route-free-v4`
in any way; their code, parameters, gold fixtures, and the four safety labels stay
untouched and selectable. A prednisone dose fact recorded today is, and remains,
`unsupported_medication` under all four of those models — never coerced into a
hydrocortisone-equivalent multiplier, exactly as ADR-0013 §2 requires.

### 2. Shape from prednisolone's own timing; amplitude from a disclosed potency table

For a supported prednisone dose of amount `D` mg at elapsed time `tau` hours:

```text
ke_pred = ln(2) / 3.5                      # 1/h, elimination
ka_pred solves tau_peak = ln(ka/ke)/(ka-ke) = 1.5 h
equivalent_hc_mg = D * 4.0                  # disclosed clinical potency equivalence

shape_pred(tau) = 0                                           tau < 0 or tau > 36 h
shape_pred(tau) = (exp(-ke_pred*tau) - exp(-ka_pred*tau))
                   / (exp(-ke_pred*1.5) - exp(-ka_pred*1.5))  otherwise

prednisone_contribution(tau) = free_peak_10_mg_nmol_l(params)
                                * (equivalent_hc_mg / 10)
                                * shape_pred(tau)
```

`free_peak_10_mg_nmol_l(params)` is the same owner-reviewed hydrocortisone
mg-to-nmol/L calibration `hc-wake-free-v3`/`hc-mixed-route-free-v4` already use
(`WakeFreeParameters.free_peak_10_mg_nmol_l`); a future revision to that calibration
rescales both drugs consistently. `ka_pred` is solved numerically from the half-life
and peak time exactly like `hc-wake-free-v3`'s own `ka` (same bisection solver, now
exposed as `wake_pharmacokinetics.absorption_rate_for_timing` for reuse); this
resolves to `ka_pred ≈ 1.584336412708446 h⁻¹`, `ke_pred ≈ 0.198042051588556 h⁻¹`.

Hydrocortisone's own contribution (oral regular/stress plus 50/100 mg IV push) is
computed exactly as in `hc-mixed-route-free-v4`, unchanged. Both contributions are
converted to **total** serum-equivalent via the existing saturable CBG/albumin
binding equation (ADR-0026's `total_from_free`/`free_from_total`), summed, and
converted back to one combined **free-cortisol-equivalent** value — the same
free→total→sum→free pattern `hc-mixed-route-free-v4` already uses to combine its oral
and IV contributions. The hydrocortisone and prednisone shares of that combined free
value are retained exactly (proportional to their total shares, with the displayed
decimals constructed so the parts always sum exactly to the combined displayed
total), so every sample and every dose-hover tooltip can show the split.

### 3. The combined value is a glucocorticoid-equivalent scenario, not cortisol

The series is named **Modeled free-cortisol-equivalent scenario (hydrocortisone +
prednisone)**, series kind `modeled_free_glucocorticoid_equivalent_scenario` — a new
value, distinct from `hc-wake-free-v3`/`hc-mixed-route-free-v4`'s
`modeled_serum_free_cortisol_scenario`. Required safety language:

> Population-parameter modeled free-cortisol-equivalent scenario from supported
> hydrocortisone and prednisone facts—not a measured cortisol or prednisolone value,
> personal target, medication-adequacy test, or dosing guide. The prednisone portion
> uses a clinical glucocorticoid dose-equivalence scaling a prednisolone-specific
> absorption/elimination shape; it is not a direct assay of either drug.

Every sample carries both the combined value and the two drug-level components:

| Field | Meaning |
|---|---:|
| `modeled_free_cortisol_nmol_l` | Combined hydrocortisone + prednisone-equivalent value (the plotted line) |
| `hydrocortisone_modeled_free_cortisol_nmol_l` | Hydrocortisone-only share (oral + IV) |
| `regular_modeled_free_cortisol_nmol_l` / `stress_modeled_free_cortisol_nmol_l` | Hydrocortisone's own regular/stress split, unchanged in meaning from v3/v4 |
| `prednisone_modeled_free_cortisol_equivalent_nmol_l` | Prednisone-only share |
| `derived_total_cortisol_nmol_l_display` | Display-only nonlinear-binding context for the combined total, as in v4 |

`modeled_free_cortisol_nmol_l` always equals
`hydrocortisone_modeled_free_cortisol_nmol_l +
prednisone_modeled_free_cortisol_equivalent_nmol_l` exactly on the displayed decimals.
These two optional fields are `null` on `hc-wake-free-v3`/`hc-mixed-route-free-v4`
payloads, which are otherwise unchanged.

### 4. Support boundary stays explicit

v5 supports exactly: oral hydrocortisone immediate-release tablets and 50/100 mg
hydrocortisone IV push (unchanged from v4), plus oral prednisone immediate-release
tablets in mg. Medication display-name substring matching remains forbidden.
Dexamethasone, prednisolone recorded directly, delayed/modified-release prednisone,
non-oral prednisone, and any other medication/route/formulation/unit combination are
`unsupported_medication`/`unsupported_formulation`/`unsupported_route`/
`unsupported_unit`, exactly like every prior model, and contribute no line.

### 5. v5 becomes the Daily Review default

Following the precedent set by ADR-0028 (v4 superseded v3 as the default because it
strictly extended the set of supported facts), `hc-full-glucocorticoid-v5` becomes the
default when a Daily Review link or API request omits `model`. This supersedes only
the default-selection portion of ADR-0028; v1–v4 remain explicit, stable selections.
Existing shared URLs with an explicit model continue to reproduce that model.

### 6. Gold fixtures

For an isolated 5 mg prednisone dose with the default hydrocortisone calibration
(`free_peak_10_mg_nmol_l = 69.59925286939999`, i.e. 1.7 × 40.940736982 nmol/L), implementations
must match these double-precision values before display rounding (absolute tolerance
`1e-6 nmol/L` unless stated otherwise):

| Elapsed hours | `prednisone_modeled_free_cortisol_equivalent_nmol_l` |
|---:|---:|
| 0 (administration) | 0 |
| 0.25 | 59.682437209 |
| 0.5 | 96.962788080 |
| 1.0 | 131.732237576 |
| 1.5 (peak) | 139.198505739 |
| 2 | 135.080765012 |
| 4 | 96.584027189 |
| 8 | 43.910075834 |
| 12 | 19.885499635 |
| 24 | 1.846858791 |
| 36 | 0.171526351 |
| > 36 | 0 |

A simultaneous 5 mg prednisone dose at wake and a 10 mg hydrocortisone dose 9 hours
later sums exactly as two independent contributions at every instant, with
`hydrocortisone_modeled_free_cortisol_nmol_l = 0` before the hydrocortisone dose and
`prednisone_modeled_free_cortisol_equivalent_nmol_l = 0` past its 36-hour horizon.
Oral-hydrocortisone-only output is unchanged from `hc-mixed-route-free-v4` to within
`1e-6 nmol/L`. These are implemented as deterministic unit tests in
`tests/unit/test_full_glucocorticoid_pharmacokinetics.py`.

## Consequences

The owner can switch between hydrocortisone and prednisone, or take both on the same
day, and see one readable combined curve with an exact per-drug hover breakdown. The
combined number is explicitly a glucocorticoid-equivalence index, not a literal
cortisol or prednisolone concentration — the clinical potency table that makes the two
drugs comparable is a therapeutic/anti-inflammatory ratio, not a pharmacokinetic
conversion, and is disclosed as such in the model metadata and safety label. Switching
back to hydrocortisone alone, or recording only prednisone, continues to produce a
curve; v4 remains available, unchanged, for anyone who wants the pre-prednisone
behavior or a historically stable reference.

Limitations inherited from v4 (tenfold between-person variability, no body weight,
illness, or binding-protein personalization) apply unchanged. v5 adds one more:
prednisolone's own protein binding and the accuracy of a 4:1 potency ratio for an
individual are not independently validated here; the model is a comparison aid, never
a medication-adequacy test, exactly as the safety label states.

## Alternatives considered

**Two independent, never-summed lines (hydrocortisone nmol/L cortisol; prednisone its
own nmol/L prednisolone).** This was offered to, and declined by, the owner: it avoids
any potency-equivalence assumption entirely, but does not produce the single combined
curve with an hover breakdown the owner asked for. It remains available in spirit —
`hc-mixed-route-free-v4` selected alongside the prednisone-only share already present
on every v5 sample — for anyone who wants to see the two series without a combined
total.

**Reuse hydrocortisone's own absorption/elimination shape for prednisone, scaled only
by the potency ratio.** Rejected. This is exactly the "potency alone" shortcut ADR-0013
forbids; prednisolone's own published timing (slower peak, longer half-life) is
materially different and readily available, so there is no reason to borrow
hydrocortisone's.

**Model literal prednisolone nmol/L and sum it directly with cortisol nmol/L.**
Rejected. The two molecules have different glucocorticoid-receptor potency per mole;
summing raw molar concentrations would be a more precise-looking but less honest
number than the disclosed clinical-equivalence index this ADR uses.

**Keep `hc-mixed-route-free-v4` as the default and make v5 opt-in only.** Rejected.
The owner's day-to-day regimen now routinely includes prednisone, so a model-less
Daily Review visit should show the model that covers it, matching ADR-0028's own
rationale for promoting v4 over v3.
