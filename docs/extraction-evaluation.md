# Extraction evaluation

HealthCurve gates changes to the extraction prompt, gold set, and local model with a
versioned synthetic evaluation. This is a regression test for parsing into drafts; it
does not make model output a fact and does not test or provide medical advice.

The gold set is `evals/extraction/gold-v1.json`. It covers relative and overnight
times, travel and DST, decimal fractions, self-corrections, multiple events, negation,
hypotheticals, and prompt injection. Every case carries the repository's synthetic
marker. Per-field thresholds are defined in that file so a missing candidate cannot
be hidden inside one aggregate score.

`evals/extraction/baseline-qwen3-30b.json` contains schema-validated predictions from
the named local model, plus its immutable Ollama digest, prompt version, gold-set
version, and run time. CI recomputes every score from those predictions and fails for
a stale version, missing provenance, changed case set, or score below threshold. CI
does not download the 18 GB model or send health text to a hosted service.

Run the deterministic CI gate with:

```bash
make eval
```

After intentionally changing the prompt, gold set, or canonical local model, run the
model locally and review the diff before committing a replacement baseline. First
update the gold set's `prompt_version` to the current `PROMPT_VERSION`; the recorder
refuses stale gold provenance before it calls Ollama:

```bash
HC_OLLAMA_BASE_URL=http://127.0.0.1:11434 \
  uv run python scripts/evaluate_extraction.py --record
```

A recording command exits nonzero when any threshold fails. Do not lower a threshold
to bless a regression; fix the parser/prompt or document and review a deliberate
product change. Never add real Telegram messages, records, or medical documents to
the gold set.

To measure nondeterminism, run every case repeatedly. The gold set defines separate
high stability thresholds (95–100% by field); the command reports the modal-value
share for each field and exits nonzero below threshold:

```bash
HC_OLLAMA_BASE_URL=http://127.0.0.1:11434 \
  uv run python scripts/evaluate_extraction.py --stability-runs 5
```
