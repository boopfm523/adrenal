# ADR-0028: Full cortisol model v4 as the Daily Review default

Status: Accepted

## Context

HealthCurve preserves four separately versioned exposure models. The newest v4 model
keeps the complete wake-anchored oral v3 calculation and also models the owner's
supported recorded 50 mg and 100 mg intravenous-push facts. Daily Review previously defaulted to
v3 in the web application while the API and client helper defaulted to v1. The v4
selector label also exposed a long implementation boundary rather than a concise
product name.

## Decision

`hc-mixed-route-free-v4` is the default when a Daily Review link or API request omits
the model. Its user-facing name is **Full cortisol model (v4)**. The internal model ID,
revision, formulas, supported-route boundaries, evidence, and safety labels remain
unchanged and inspectable. V1, v2, and v3 remain available as explicit selections.
This supersedes only the initial-default and absent-selector portions of ADR-0024.

## Consequences

- New and model-less Daily Review visits show the model that covers the currently
  supported oral and intravenous-push facts.
- Frontend and API defaults no longer disagree.
- Existing shared URLs with an explicit model continue to reproduce that model.
- The concise label is not a claim that every medication, amount, formulation, or
  route is modeled; unsupported facts remain visible and explicitly excluded.

## Alternatives considered

- Keep v3 as the web default and v1 as the API default. Rejected because identical
  model-less requests produced inconsistent results.
- Rename the internal v4 ID. Rejected because it would break reproducibility and
  historical references without changing the calculation.
