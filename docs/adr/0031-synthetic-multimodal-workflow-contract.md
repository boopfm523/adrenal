# ADR-0031: Synthetic contract before image/PDF workflow replacement

**Status:** Accepted — 2026-08-27

## Context

The owner accepted the Qwen3.8 text-model cutover in ADR-0030 but explicitly described
HealthCurve's current image/PDF experience as too poor to use as the comparison
baseline for a replacement. The existing vision regression set contains two clean,
single-page lab layouts. It does not represent phone photographs, rotation and crop
damage, mixed multi-page documents, handwriting, conflicting printed fields, or the
important fail-closed paths.

Image and PDF work also crosses different trust boundaries. Native PDF text, OCR
tokens, rendered page images, and vision output need different tools and provenance.
Choosing another multimodal model before defining that contract would optimize a
model score without proving that the complete workflow is private, reviewable, or
safe.

## Decision

Approve `evals/vision/workflow-gold-v2.json` as the source-independent workflow
contract for the redesign. It contains synthetic descriptions only and must cover:

- native PDFs, scanned PDFs, and phone photographs;
- deterministic embedded-text and OCR routes, vision fallback, and rejection;
- rotation, perspective, cropping, mixed multi-page layouts, conflicting units and
  reference ranges, handwriting/non-tabular content, prompt injection, and model
  unavailability;
- source checksum, page, extraction tier, extractor version, evidence coordinates,
  normalization transforms, and model/prompt identity where applicable; and
- confirmation-required drafts, no direct fact writes, networkless parsers, and
  content-free logs.

The executable contract validator runs in normal evaluation and CI. It rejects
missing source/route/feature coverage, missing provenance, unsafe candidate states,
private-data claims, and parser network access. This contract is distinct from
`gold-v1.json` and its historical `qwen3-vl:30b` output. The old score remains useful
as regression evidence for the existing implementation but is not an acceptance
threshold for the redesign.

Implement the replacement as the bounded stages in
`docs/multimodal-lab-workflow.md`. Candidate vision models may be compared only after
the deterministic and normalization stages produce stable synthetic inputs. No
vision-model cutover is approved by this ADR. `qwen3-vl:30b` remains selected until a
candidate passes the expanded rendered-fixture gate and the owner explicitly accepts
the cutover.

## Consequences

Positive:

- Model selection is evaluated as one fallback inside a complete ingestion workflow.
- Failure, privacy, provenance, and confirmation behavior are first-class gates.
- The repository never needs real medical documents or owner screenshots for tests.
- A weak legacy score cannot force the replacement to preserve a weak design.

Negative:

- The contract validator proves scope and invariants, not OCR or model accuracy.
- Rendered synthetic fixtures and candidate baselines still have to be implemented
  before a vision-model decision.
- Phone-photo normalization adds a new input path and attack surface that must remain
  isolated from fact writes.

## Alternatives considered

**Compare another model against the existing two-case baseline.** Rejected because it
would omit the workflows the owner actually finds unreliable.

**Commit redacted owner documents as fixtures.** Rejected. Redaction mistakes are
plausible, and synthetic cases can exercise the required shapes without retaining
private source material.

**Use a single multimodal model for every PDF and image.** Rejected. Exact embedded
text and deterministic OCR evidence remain faster and more auditable when adequate.

**Switch Qwen3.8's multimodal capability on for documents immediately.** Rejected.
The accepted text runtime and the document-vision fallback have separate prompts,
schemas, performance profiles, and release gates.
