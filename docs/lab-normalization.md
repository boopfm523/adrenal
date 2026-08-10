# Curated laboratory normalization

HealthCurve keeps the source analyte, value, unit, range, and provider flag exactly as
recorded. A separate deterministic layer may add a canonical analyte code, value, unit,
and named method. Derived fields never replace the source fact and never determine
whether a result is abnormal.

Version `hc-lab-normalization-v1` deliberately covers only common CBC fields; sodium,
potassium, chloride, bicarbonate/total CO2, BUN, creatinine, glucose, calcium, and
magnesium; and cortisol. Unknown analytes remain valid source facts. A recognized
analyte with an unsupported or missing unit gets a canonical code but no derived value.

## Cortisol boundary

Cortisol trends are separated by specimen type and unit. Collection time, timezone,
source range, and report provenance remain visible. HealthCurve does not label a
cortisol result normal/abnormal, infer adrenal function, or recommend medication.
Cortisol concentration in `mcg/dL` is converted to `nmol/L` with the published factor
27.6, while specimen types remain in separate trend groups. Timed urine (`mcg/24 h`),
free-cortisol, and other incompatible units are preserved but not converted by this
rule.

## Conversion references

- Mayo Clinic Laboratories, *International System of Units (SI) Conversion*:
  <https://www.mayocliniclabs.com/order-tests/si-unit-conversion.html>
- NIST, *SI Units — Volume* (including 10 dL = 1 L):
  <https://www.nist.gov/pml/owm/si-units-volume>
- CDC CLIAC, *Units of Measure Variations in Collection of Lab Test* (documents the
  wide range of CBC source-unit spellings):
  <https://www.cdc.gov/cliac/docs/addenda/cliac0313/13A_CLIAC_2013March_UnitsOfMeasure.pdf>

Every rule uses `Decimal`, a fixed output scale, and an explicit versioned method. New
aliases or factors require synthetic regression tests and a new registry version when
the mathematical meaning changes.

After installing a new registry version, recompute derived fields for existing records:

```bash
docker compose run --rm api python -m healthcurve.cli normalize-labs
```

The command is idempotent, leaves every source string unchanged, and writes only a
privacy-safe audit summary with the version and number of derived rows changed.
