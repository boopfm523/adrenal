# Qwen3.8-27B Q8 qualification and reversible cutover

This runbook implements accepted ADR-0030 and Beads epic `hc-th29`. It evaluates the
text model only. It does not select Qwen3.8, merge its branch, deploy production, or
change the separate `qwen3-vl:30b` image/PDF model.

## 1. Update host-native Ollama

This Mac uses `/Applications/Ollama.app`, with its CLI linked at
`/usr/local/bin/ollama`. Use Ollama's official macOS installer and verify the result:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama --version
```

Ollama 0.33.0 is the recommended host version. The pulled Q8 artifact declares
0.32.12 as its minimum, which is also the fail-closed compatibility boundary used
by preflight. The macOS menu-bar application's **Restart to update** action is an
equivalent supported update path.

## 2. Install the candidate beside the current model

```bash
ollama pull qwen3.8:27b-q8_0
```

Do not remove `qwen3:30b`; it is the selected default and rollback model. The candidate
is approximately 30 GB. Model inventory and immutable digests can be inspected with
`ollama list` and `ollama show qwen3.8:27b-q8_0`.

## 3. Preflight and qualify without selecting it

```bash
make qwen38-preflight
make qwen38-qualify
```

Preflight checks the Ollama version, exact candidate tag, immutable digest,
`think:false`, the 24,576-token bounded context, and JSON-schema output through
HealthCurve's native Ollama adapter.

Qualification runs the all-synthetic extraction, chatbot, and generated-analysis
gates. It deliberately does not run the image/PDF vision suite. Evidence is written
under `evals/candidates/qwen3.8-27b-q8_0/`; existing `qwen3:30b` baselines are never
overwritten. A failed suite leaves the candidate unqualified. Do not lower a safety
threshold or add real health data to make it pass.

## 4. Review and owner decision

Review `qualification.json` plus each synthetic suite report. The owner-decision Bead
is `hc-xtnt`. Before the owner accepted the cutover on 2026-08-27, the following gates
remained in force:

- `HC_OLLAMA_MODEL` remains `qwen3:30b`;
- the candidate branch remains unmerged;
- no service is recreated with Qwen3.8 selected; and
- ADR-0030 remains Proposed.

## 5. One-command cutover after approval and merge

```bash
make qwen38-activate
```

The command refuses to proceed unless every recorded suite passed and the currently
installed candidate digest exactly matches the qualified digest. It atomically changes
only `HC_OLLAMA_MODEL` in `.env`, preserves every other value and the file mode, then
recreates only the API and worker services through the default Compose topology used by
the private runtime. Opt-in Compose overlays are not required. Vision remains
`qwen3-vl:30b`.

After activation, verify `/health/ready`, one synthetic Telegram extraction, and the
chatbot's ordinary and failure paths before relying on it. Keep real owner text out of
logs and committed evidence.

## Rollback

```bash
make qwen3-rollback
```

Rollback first verifies that `qwen3:30b` is still installed, atomically restores its
selection, and recreates the same two services. It does not delete either model or
alter facts, plans, chat history, prompts, or evaluation reports.

## Qualification result: 2026-08-26

The local Q8 artifact passed all synthetic HealthCurve gates with thinking disabled:

- model: `qwen3.8:27b-q8_0`
- immutable digest: `8f5fb6b71ea00052cbe8545738c55ce61112c4e571cb60ca4dad00b131766039`
- evaluated runtime: Ollama 0.33.1 (artifact-declared minimum 0.32.12)
- bounded context: 24,576 tokens, approximately 30 GB resident and 100% GPU in
  `ollama ps`
- extraction: pass, all field scores 1.000, 15 cases in 559.725 seconds
- chatbot: pass, 5 cases in 129.174 seconds
- generated analysis: pass, 4 cases in 78.963 seconds

The unbounded Qwen3.8 default was also measured during investigation: Ollama allocated
the full 262,144-token context and approximately 45 GB. The checked-in context bound
prevents that. A multi-event extraction took 61.968 seconds, so extraction now has a
hard 120-second read ceiling; ordinary extraction cases were generally 34–36 seconds.

For owner acceptance testing, `HC_OLLAMA_KEEP_ALIVE_S` may be raised from its
five-minute default to at most 3600 seconds. HealthCurve sends this bounded value only
on text-model API requests to host-native Ollama; it does not start the optional
Compose Ollama service and does not pin the separate vision model. This can remove a
cold-load delay between sporadic requests, but it cannot remove the dense model's
34–36 second generation cost. Clear single Telegram temperature, blood-pressure,
weight, and explicit-dose statements therefore use deterministic validation and the
same confirmation-draft contract before model fallback.

The qualification was a technical **PASS**. After live private-runtime acceptance
testing, the owner explicitly accepted the candidate's slower model-backed responses
on 2026-08-27 and approved the guarded cutover and branch merge. `qwen3:30b` remains
installed as the one-command rollback model.
