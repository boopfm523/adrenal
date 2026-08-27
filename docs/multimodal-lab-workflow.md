# Image and PDF workflow redesign

ADR-0031 approves this scoped plan after the Qwen3.8 text-model cutover. It redesigns
the workflow rather than treating the current two-layout vision baseline as the
quality target. It does not select or activate a new vision model.

## Fixed boundaries

- Inputs are untrusted health artifacts. Parsers, renderers, OCR, and normalization
  run locally without network access.
- Native Ollama remains outside Docker under ADR-0017. Application containers may
  call it only through the private host endpoint; no Ollama container is added.
- Deterministic embedded text is preferred, then OCR, then vision for unresolved page
  relationships. A later tier never overwrites stronger source evidence.
- All extracted rows are confirmation-required drafts. Models, OCR, and parsers have
  no fact or physician-plan write path.
- Original printed values, units, ranges, page numbers, and evidence boxes are
  preserved. Conflicts and missing fields remain visible; the workflow does not infer
  a diagnosis, normalize a value into authority, or invent a field.
- Repository fixtures and candidate evaluations are synthetic. Raw content, page
  images, prompts, and model bodies are excluded from routine logs.

## Implementation slices

1. **Unified intake and normalization.** Add bounded PNG/JPEG/HEIC phone-photo intake
   beside hostile-PDF validation. Decode into a fresh inert raster, discard active
   metadata, preserve source checksum/media metadata, and record every orientation,
   crop, or perspective transform. Reject unsupported, encrypted, oversized, or
   decompression-bomb inputs before extraction.
2. **Page-wise deterministic routing.** Keep native PDF geometry first. Improve OCR
   layout recovery and decide adequacy per page so a mixed document can use embedded
   text on one page and OCR or vision on another. Preserve lower-tier candidates and
   their coordinates rather than flattening them into a prompt-only blob.
3. **Draft and provenance contract.** Return a versioned candidate envelope containing
   source checksum, page, source/render coordinate spaces, extraction tier, extractor
   versions, normalization transform, confidence/flags, and immutable model/prompt
   identity. Incomplete or conflicting content is unparsed or flagged, never filled.
4. **Vision adapter qualification.** Render the approved v2 scenarios into varied
   synthetic documents and photos. Run each local candidate with thinking disabled,
   bounded context, temperature zero, schema validation, explicit timeout/outage
   outcomes, and one large model loaded at a time. Measure field fidelity, row count,
   page/box evidence, refusal to follow page instructions, failure typing, and latency.
5. **Review experience.** Show the inert source preview beside each candidate, make
   the cited page/region obvious, expose the extraction tier and warnings in plain
   language, and support confirm/edit/reject with keyboard and mobile accessibility.
6. **Release and rollback.** Require the executable workflow contract, rendered v2
   candidate baseline, unit/integration/security tests, container topology check, and
   owner acceptance. Select a model by digest through a reversible setting; keep the
   accepted previous vision model installed until rollback is verified.

## Evaluation and evidence

Run the contract gate without loading a model:

```bash
uv run python scripts/evaluate_multimodal_workflow.py
```

The gate must report all three input families, all four routes, and every required
redesign feature. `make eval` runs it with the existing extraction, analysis, chatbot,
and historical vision checks.

The next model-quality baseline must be generated entirely from rendered v2 fixtures.
It may not copy values, names, messages, or images from the owner's database, uploads,
Telegram history, or screenshots. A candidate failure leaves explicit unparsed
evidence and cannot silently fall back to a direct write.

## Completion gates for a future cutover

- Every v2 scenario has a generated artifact and deterministic expected outcome.
- Provenance round-trips through API and review UI for native, OCR, and vision paths.
- Rotation/crop transforms map model evidence back to the displayed preview.
- Model outage, timeout, invalid schema, and prompt injection fail visibly and retain
  lower-tier evidence.
- No parser container has network access; Ollama is host-native and private.
- Mobile/desktop review, keyboard flow, and evidence highlighting pass visual and
  accessibility checks.
- The owner explicitly approves any selected vision model after reviewing synthetic
  results and measured latency.
