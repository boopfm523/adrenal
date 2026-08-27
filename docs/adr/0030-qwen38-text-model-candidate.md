# ADR-0030: Qualification-gated Qwen3.8 text-model candidate

**Status:** Proposed — 2026-08-26

## Context

HealthCurve currently uses `qwen3:30b` for private text extraction, chatbot planning
and answers, and generated analysis. That model is selected by `HC_OLLAMA_MODEL` and
served by host-native Ollama under ADR-0017. The separate `qwen3-vl:30b` vision model
is selected by `HC_OLLAMA_VISION_MODEL`.

Qwen3.8-27B is a newer dense, native multimodal model with official Ollama Q8 and MLX
packages. The target Apple M4 Pro has 64 GB unified memory, which is sufficient for
the approximately 30 GB Q8 weights at HealthCurve's bounded contexts. However,
Qwen3.8-specific serving integrations are new, and similar model size does not imply
similar latency: the current 30B model is mixture-of-experts while Qwen3.8-27B is
dense.

A blind tag replacement is inappropriate. HealthCurve depends on immutable model
identity, `think:false`, JSON-schema-constrained output, Pydantic validation, safe
failure states, and all-synthetic extraction, chatbot, and analysis gates.

## Proposed decision

Evaluate `qwen3.8:27b-q8_0` through the existing native Ollama adapter as a non-default
candidate. Recommend Ollama 0.33.0 and require at least the candidate artifact's
declared minimum (0.32.12), one loaded model at a time, thinking off,
and the existing bounded 24,576-token maximum chat context during qualification.

Qualification must:

1. resolve and record the candidate's immutable local digest;
2. pass a synthetic structured-output preflight using the same `/api/chat` path as
   HealthCurve;
3. pass the existing synthetic extraction, chatbot, and generated-analysis gates
   without weakening thresholds;
4. record per-suite wall time and candidate provenance in a separate report; and
5. leave `qwen3:30b` as the repository and runtime default.

A later explicit owner decision is required to change this ADR to Accepted, merge the
candidate branch, or activate the model. Activation is an atomic `.env` selection
guarded by the qualified digest; rollback restores `qwen3:30b`. The vision selection
remains `qwen3-vl:30b` and image/PDF redesign is separate work.

## Consequences

Positive:

- The stronger candidate can be evaluated using HealthCurve-specific behavior rather
  than vendor benchmarks.
- A later cutover is one guarded command and does not require an application-code
  change.
- The current model remains installed and immediately selectable for rollback.
- Candidate reports contain only synthetic fixtures and non-sensitive model metadata.

Negative:

- Keeping both models consumes roughly 49 GB before shared or vision-model blobs.
- A dense 27B Q8 model may respond more slowly than the current 30B MoE Q4 model.
- Qwen3.8 and its Ollama/MLX serving paths are new enough that regressions may still
  appear after qualification.
- Qualification does not improve the existing image/PDF workflow.

The 2026-08-26 qualification, repeated after upgrading the runtime to Ollama 0.33.1,
passed extraction, chatbot, and generated-analysis gates for digest
`8f5fb6b71ea00052cbe8545738c55ce61112c4e571cb60ca4dad00b131766039`.
The candidate required a 24,576-token default context bound to avoid a 45 GB
full-context allocation and took roughly 35–37 seconds for ordinary extraction cases.
Therefore this ADR remains Proposed pending an explicit owner decision about the
latency tradeoff.

## Alternatives considered

**Replace the default immediately.** Rejected because model availability does not
prove HealthCurve schema, safety, and planner compatibility.

**Use direct MLX or vLLM-Metal first.** Deferred. The current Ollama adapter already
provides private connectivity, structured output, identity, timeouts, and failure
typing. A new provider would add integration risk before model quality is known.

**Use SGLang on the Mac.** Deferred because its Apple backend remains a newer,
source-built integration and HealthCurve has no multi-user throughput requirement.

**Replace the vision model at the same time.** Rejected for this decision. The owner
has identified the current image/PDF workflow as needing a later redesign rather than
a baseline comparison bundled into the text-model cutover.
