# ADR-0010: Deterministic-first local document ingestion and task-specific models

**Status:** Accepted — 2026-08-09

## Context

HealthCurve needs to ingest laboratory PDFs without turning a parser, OCR engine, or
language model into an authority over the health record. A PDF may contain embedded
text, scanned pages, malformed objects, active content, prompt-injection text, or a
table whose visual alignment carries meaning. A single extraction method cannot safely
cover all of those shapes.

The current default model, `qwen3-coder`, is optimized for agentic software-engineering
work and was pretrained primarily on code. It accepts text, not page images. That is a
poor default for Telegram health-language extraction and cannot serve as the document
vision fallback. The official Ollama catalog describes the general Qwen3 family as an
instruction-following text model and Qwen3-VL as a text-and-image model with document
and OCR capabilities. Ollama supports JSON-schema-constrained structured output for
both text and vision calls.

The target Mac is an Apple M4 Pro with 64 GB unified memory. Ollama running inside
Docker Desktop cannot use Metal, while the native Ollama service can. ADR-0003 still
governs connectivity: model traffic must remain on a private authenticated path, and a
native service must never be exposed on all interfaces merely to make Docker reach it.

Primary references:

- [pdfplumber](https://github.com/jsvine/pdfplumber) extracts text, words, positions,
  and tables from machine-generated PDFs and explicitly works best on those rather
  than scans.
- [Tesseract](https://github.com/tesseract-ocr/tesseract) is a local OCR engine for
  images; major version 5 is the stable line.
- [qpdf CLI documentation](https://qpdf.readthedocs.io/en/stable/cli.html) documents
  structural checks, encryption detection, JSON inspection, warnings, and error exit
  behavior. It also cautions that a successful check cannot prove a PDF harmless.
- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
  supports JSON Schema for text and vision, while
  [Ollama vision](https://docs.ollama.com/capabilities/vision) accepts images as
  bytes/base64 rather than PDF documents.
- The official model entries list [`qwen3:30b`](https://ollama.com/library/qwen3) as a
  19 GB text model and [`qwen3-vl:30b`](https://ollama.com/library/qwen3-vl) as a 20 GB
  text-and-image model. Qwen3-VL requires Ollama 0.12.7 or later.

## Decision

### 1. Route every document through three ordered tiers

HealthCurve will use the first tier that produces adequate, internally consistent
evidence. A later tier may propose candidates but may never silently overwrite an
earlier tier's values.

1. **Embedded text and geometry:** validate the document with qpdf, then use
   `pdfplumber` to extract characters, words, bounding boxes, and table candidates.
   Deterministic code groups explicit cells and preserves the lab's original value,
   unit, reference range, flags, page, and bounding box verbatim. This is always tried
   first.
2. **OCR:** when a page has no adequate text layer, render only that page to a bounded
   PNG with Poppler and run Tesseract 5 with the required local language pack. Retain
   TSV/HOCR coordinates and confidence for review. OCR output does not replace a good
   embedded text layer merely because the strings differ.
3. **Vision fallback:** use `qwen3-vl:30b` only for pages whose table relationships
   remain unresolved after deterministic extraction/OCR. Send the rendered page image,
   the lower-tier tokens, and a data-only prompt through the existing private Ollama
   boundary. Require a strict JSON Schema and page/bounding-box evidence per proposed
   field.

The vision model receives page images, not raw PDFs. No PDF parser, renderer, or OCR
process gets network access; the vision request uses only ADR-0003's private model
path. No tier diagnoses, interprets a result, converts units as if authoritative,
fills a missing field, or decides that a candidate is a fact.

### 2. Use task-specific local models

- **Text extraction model:** `qwen3:30b`, approximately 19 GB in the current official
  Ollama build. It replaces `qwen3-coder` as `HC_OLLAMA_MODEL` for Telegram and
  structured text extraction because it is the general instruction-following model;
  `qwen3-coder` remains a development tool, not a HealthCurve runtime dependency.
- **Document vision model:** `qwen3-vl:30b`, approximately 20 GB. It is selected over
  the 8B variant because reading small numerical cells is the risk-sensitive fallback,
  and the target 64 GB machine can run the 30B quantization locally.

The required pulls are therefore:

```bash
ollama pull qwen3:30b
ollama pull qwen3-vl:30b
```

Budget up to **39 GB additional disk** if Ollama cannot reuse existing blobs. Model
tags can move, so every accepted draft records the resolved name and digest. Deployment
inventory records those digests as well. The installed Ollama versions (native 0.32.5
and container 0.32.6 when this ADR was accepted) exceed Qwen3-VL's 0.12.7 minimum.

Only one large model is loaded at a time. Vision is a queued fallback and is unloaded
after its document job; it does not run concurrently with routine text extraction.
Context is bounded to the relevant page and known schema, not the model's advertised
maximum. `temperature=0`, JSON Schema, Pydantic validation, deterministic field checks,
and the existing fact/plan write denial all remain mandatory.

Model choice is not an accuracy claim. `hc-h5r.8` must gate prompt/model changes on a
synthetic and explicitly consented redacted gold set covering decimals, inequalities,
units, reference ranges, flags, multi-column tables, negation, ambiguity, and prompt
injection. A model that fails the gate remains unavailable; it is not allowed to
degrade silently to lower-quality accepted output.

### 3. Treat every PDF as hostile input

The document worker will enforce all of the following before the upload or extraction
issues can close:

- authenticate and owner-scope the upload; generate storage IDs and treat filenames as
  untrusted display metadata;
- require PDF magic and an allowed media type, hash while streaming, cap the source at
  25 MiB, cap pages at 100, and reject encrypted/password-required documents initially;
- run qpdf structural/encryption/JSON inspection with a timeout; reject errors,
  JavaScript, launch actions, embedded files, and unexpected interactive content;
- execute qpdf, pdfplumber, Poppler, and Tesseract in a dedicated non-root worker with
  no network, no Linux capabilities, a read-only root filesystem, bounded CPU, memory,
  processes, wall time, rendered dimensions, total pixels, and tmpfs scratch space;
- never invoke a shell with a submitted filename and never allow a submitted path to
  select an output location;
- never render the source PDF inline in a browser. Review uses generated inert PNG page
  previews; an explicit source download is attachment-only with `nosniff` headers;
- delete scratch files on success, rejection, timeout, cancellation, and worker restart;
- log only an opaque document/job ID, stage, duration, page/candidate counts, and reason
  code—never filenames, extracted text, values, page images, or model bodies.

Qpdf validation and container isolation reduce risk; neither is described as malware
scanning or proof that a document is safe. Parser/renderer dependencies stay pinned and
are included in vulnerability scanning and image rebuild policy.

### 4. Retain the source for provenance, with explicit deletion

The exact uploaded PDF is retained as a C4 health artifact until the owner deletes the
document/panel. It is stored outside the web root under an opaque ID with byte size,
SHA-256, media type, owner, upload time, and original display name in database metadata.
It is included in encrypted backups and the complete owner export.

Retention is necessary to show the source during review, prove what was confirmed, and
re-extract after a parser/model upgrade without asking the owner to find the file
again. Facts retain source-document checksum, page, bounding box, extraction tier, and
extractor/model versions. A safe raster preview may be retained with the source.

Raw OCR text, model prompts/responses, and temporary page images are C9 draft material.
They are purged when a draft is confirmed, cancelled, or expires; the minimal source
manifest remains. Deletion removes source and derivatives after checking references and
records the action in the audit log. Backup copies expire under the disclosed backup
retention schedule.

### 5. Confirmation remains the authority boundary

Every extracted lab row is a draft candidate displayed beside its page evidence. The
owner may edit, reject, or confirm each candidate. Only confirmation writes a C4 fact;
neither OCR nor either model connection can write facts or plans. Missing, unreadable,
conflicting, and low-confidence fields stay visibly missing or flagged.

Physician-approved instructions found in a PDF are not automatically a plan. They may
be attached as source material, but plan approval still requires the separate human
approval workflow and provenance required by SAFE-16.

## Consequences

Positive:

- Most digital lab PDFs take a fast, auditable path with no model involved.
- Scans remain local and gain word-level evidence rather than an opaque block of text.
- Vision is available for layout, but cannot bypass schema validation or confirmation.
- Exact source retention supports audit, correction, and repeatable re-extraction.
- The runtime text model now matches the natural-language task instead of the coding
  task for which Qwen3-Coder was optimized.

Negative / costs:

- The two required Ollama models may consume 39 GB, plus temporary render space and the
  retained document/preview.
- The fallback may be slow because models are loaded sequentially and page rendering is
  deliberately bounded.
- A dedicated document worker and several pinned native tools increase maintenance and
  vulnerability-patching work.
- Source retention increases the volume of high-sensitivity data in backups and makes
  the future deletion workflow mandatory rather than optional.
- Some valid interactive or encrypted PDFs will be rejected and must be exported or
  printed to a simpler PDF by the owner before import.

## Alternatives considered

**Send PDFs to a hosted OCR or LLM API.** Rejected. It sends C4/C9 medical data to a
third party and contradicts ADR-0003 without an explicit new owner decision.

**Use Qwen3-Coder for all extraction.** Rejected. Its official description and training
focus are software engineering, and it has no image input. Code skill is irrelevant to
the record-extraction task.

**Use only `qwen3-vl` for text and images.** Rejected. It would make a probabilistic
model the default even when the PDF exposes exact characters and coordinates.

**Use `qwen3-vl:8b`.** Lower disk and memory cost, but numerical cell reading is the
risk-sensitive fallback and the target machine has capacity for 30B. It remains a
candidate only if the 30B model cannot meet latency/availability constraints and the
same evaluation gate proves the smaller model.

**Use OCRmyPDF as the complete pipeline.** It is useful for searchable archival PDFs,
but HealthCurve needs page tokens, boxes, confidence, and an immutable original—not a
rewritten PDF/A as the source of truth. Direct bounded rasterization plus Tesseract has
the smaller required contract.

**Discard source PDFs after extraction.** Rejected. It prevents source-side review and
repeatable re-extraction and weakens the provenance of a confirmed lab value.
