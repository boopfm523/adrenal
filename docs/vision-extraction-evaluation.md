# Vision extraction evaluation

The live vision gate uses only generated images described by
`evals/vision/gold-v1.json`. It covers the two layout families observed in the
owner-approved local samples—result cards and a dense table—without copying any real
medical value or document content into the repository.

The checked-in baseline records the exact `qwen3-vl:30b` Ollama digest, prompt version,
gold-set version, predictions, and generation time. Exact thresholds cover candidate
count, analyte, value, unit, reference range, page evidence box, and prompt-injection
handling. Every prediction remains a confirmation-required AI draft; the evaluation
has no fact or plan write path.

After intentionally changing the vision prompt, gold set, or selected model:

```bash
HC_OLLAMA_BASE_URL=http://127.0.0.1:11434 \
  uv run python scripts/evaluate_vision.py --record
uv run python scripts/evaluate_vision.py
```

The loopback override is for recording from the macOS host. The normal `.env`
value (`host.docker.internal`) remains correct for the application containers.

Review the generated baseline diff before committing it. Normal `make check` verifies
the recorded baseline without loading the 19 GB model or transmitting any data.
