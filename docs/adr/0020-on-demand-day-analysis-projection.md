# ADR-0020: On-demand daily AI analysis uses a fingerprinted projection

Status: Accepted

## Context

The owner wants an optional “analyze this day” action that can inspect relationships
across the daily HealthCurve and other records that may not be visible on the chart.
Sending raw Garmin streams verbatim would create a large, noisy prompt, while sending
only daily averages would discard the timing needed for useful descriptive analysis.
Late Garmin syncs and fact corrections can also make a previously generated narrative
out of date.

The feature must preserve the separation among recorded facts, physician-approved
plans, and generated analysis. It must not turn an association into causation, infer a
cortisol measurement or medication requirement, recommend dosing, or send private
health data to a cloud model.

## Decision

HealthCurve calculates a selected-day projection on demand. It includes all supported
record domains and the approved plan active during the day. Every dense intraday Garmin
and theoretical-exposure sample contributes to deterministic 15-minute local-time
buckets; sparse events retain their exact recorded time and value. Missing domains are
listed explicitly. Exact coordinates are withheld even from the private model.

The JSON projection receives a SHA-256 source-day fingerprint and is sent only through
the configured private Ollama boundary from ADR-0017. A deliberate Analyze action may
include sensitive diary and life-event text because the owner asked to use all
selected-day data; the UI states this before the call. Stored output remains in
`ai.ai_analysis` as a `daily_summary`, with compact provenance containing the
fingerprint, model digest, prompt version, schema version, date, timezone, availability,
missingness, and citations. Facts and plans are read-only inputs.

The full projection is transient model input rather than a second stored copy of the
owner's facts. The saved analysis retains the source-record manifest plus a compact
availability, missingness, date/timezone, and fingerprint provenance record. The latest
saved analysis is compared with a freshly calculated fingerprint whenever
it is read. If late or corrected data changes the selected-day projection, the UI marks
the narrative stale and offers regeneration. Timeout, unavailable-model, refusal, and
schema/safety-validation failures return a generic visible fallback and save nothing.

## Consequences

- The model can compare intraday shapes without receiving thousands of repetitive raw
  points, and every observed dense sample still contributes deterministically.
- Generating or deleting/regenerating an interpretation cannot mutate facts or plans.
- The transient projection and retained AI narrative contain sensitive health content;
  retained provenance follows the existing AI-analysis export, backup, and deletion
  controls without duplicating the full source facts.
- A fingerprint proves which source revision was analyzed but is not a clinical
  signature and contains no readable health values.
- Bucket-level observations are descriptive; sub-bucket changes may be hidden and must
  not be presented as causal or diagnostic findings.

## Alternatives considered

**Send every raw sample.** Rejected because prompt size and noise scale with Garmin
cadence and make model behavior less predictable.

**Analyze only the rendered SVG or a screenshot.** Rejected because it loses exact
values, missingness, provenance, and non-chart records.

**Cache a narrative without source revision tracking.** Rejected because later Garmin
syncs and corrections would silently leave an obsolete result looking current.
