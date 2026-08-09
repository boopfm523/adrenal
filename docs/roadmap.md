# HealthCurve: remaining work

**This document explains the plan. Beads holds the plan.** Nothing here is a checklist
to tick off by hand — run `bd ready` and work the top of the list. This file exists so
that you, or an agent picking this up cold in a new session, understand *why* the
backlog is ordered the way it is.

## How to resume, from nothing

```bash
cd /Users/jeff/Documents/adrenal
make up                 # start the stack
bd ready                # what is claimable right now
bd show <id>            # read one before starting it
```

Then follow the loop in [beads-workflow.md](beads-workflow.md): claim it, note your
approach, build the smallest complete slice with tests, record evidence, close it,
pull the next. Do not invent work that is not in Beads; if you discover some, create
a linked issue rather than doing it invisibly.

Before closing anything: `make check` must exit 0.

## Where the build actually is

| Phase | State |
|---|---|
| **P0 Foundation** | Done except four spikes (Garmin, backup design, UI IA, Telegram PoC) |
| **P1 Trusted record** | **Done.** Auth, plans, doses, events, episodes, corrections, audit, timeline, export, emergency page |
| **P2 Telegram + AI** | Mostly done. Missing: long polling, draft *edit*, evaluation harness |
| **P3 Dashboard, labs, reports** | **Not started.** 16 issues. This is the bulk of the remaining work |
| **P4 Integrations** | Not started, gated on the Garmin spike |
| **P5 Production** | Not started. 12 issues |
| **P6 Hardening** | Not started |

46 open issues. The API works and holds real data today; there is **no web interface**,
so everything currently goes through the API, the CLI, or Telegram.

---

## Three decisions to make first

These are `type: decision` issues at priority 0 because they change what gets built.
Each is small — an ADR, not an implementation.

### 1. Tailscale hosting breaks the Telegram webhook

You want to host on your own machine and reach it over Tailscale. That is a good fit
for this product and it makes the threat model *stronger* — nothing is on the public
internet at all. But it breaks one thing:

**Telegram webhooks require a publicly reachable HTTPS endpoint with a valid
certificate.** Telegram's servers make an inbound connection to you. Over Tailscale
there is no inbound path from Telegram, so the webhook can never be delivered.

Two ways out:

| | **Long polling** (recommended) | **Tailscale Funnel** |
|---|---|---|
| How | Your worker calls Telegram's `getUpdates` outbound | Tailscale publishes your service to the internet with a real cert |
| Exposure | Nothing inbound, nothing public | Your API becomes publicly reachable |
| Cost | A polling loop, a few seconds of latency | None, but it undoes the privacy benefit |
| Fits your setup | Yes | Works, but contradicts the reason you chose Tailscale |

I recommend **long polling**. The bot code, the allow-list, the deduplication and the
whole draft flow are unchanged — only the transport differs. The webhook endpoint stays
in the codebase for a future public deployment.

Also note: Tailscale can issue a real HTTPS certificate for your machine's `*.ts.net`
name, so you get a browser-trusted padlock without exposing anything. That is what the
hosting ADR should specify instead of ACME over port 80.

Issues: **ADR: Tailscale-only hosting** and **ADR: Telegram long polling**, then the
long-polling worker.

### 2. PDF lab results need models you do not have

See the next section — this is the largest of the three decisions.

### 3. The extraction model is probably wrong

`HC_OLLAMA_MODEL` defaults to `qwen3-coder`. That is a **code** model, and it is
**text-only**. It cannot read a PDF page image at all, and a general instruct model
will extract natural language better. Both belong in the same ADR as the PDF decision.

---

## Reading lab PDFs

You asked whether you need other models. **Yes — at least one, possibly two.** But the
first step needs no model at all, and that matters: a deterministic parse is more
trustworthy than a model reading numbers off a page.

### Three tiers, in order

**Tier 1 — embedded text (no model).** Most lab PDFs from a portal are digital
documents with real text in them. `pdfplumber` extracts the words and their positions,
and the analyte table can be parsed by rules. This is exact, fast, free, and auditable.
Try this first, always.

**Tier 2 — OCR for scans (no LLM).** A photographed or scanned report has no text
layer. `tesseract` (a system package, not a model) turns the page into text. Accuracy
on clean lab printouts is good; on a phone photo of a crumpled page it is not.

**Tier 3 — vision model for awkward layouts.** Multi-column reports, values split
across a page, or footnoted reference ranges defeat both of the above. A
vision-language model reads the page image and returns structured rows.

### Models to pull

```bash
# Text extraction and analysis -- replaces qwen3-coder for this job
docker compose exec ollama ollama pull qwen3:8b

# Vision, for tier 3
docker compose exec ollama ollama pull qwen2.5vl:7b
```

Rough disk cost: 5–6 GB each at these sizes, more for larger variants. **Verify the
exact tags before relying on them** — model names on Ollama change, and my knowledge
of the library has a cutoff:

```bash
docker compose exec ollama ollama list          # what you already have
# and check https://ollama.com/library for current vision-capable tags
```

On a Mac, note that Ollama in Docker has no GPU access — it runs on CPU and will be
slow. Running Ollama natively on the host and pointing the app at it over the Tailscale
interface is the faster arrangement, and ADR-0003 already allows for exactly that
(topology B). The startup check requires a private address, which a Tailscale IP is.

### What the PDF work actually involves

Reading the page is the easy part. Four issues cover it:

- **Schema** — `LabPanel` and `LabResult` storing the lab's own value, unit and
  reference range verbatim. Normalized units are separate derived fields. This matters
  because reference ranges differ between labs, and a value is meaningless without the
  range it was reported against.
- **Upload and storage** — a PDF is hostile input. Parsers have a long CVE history, so:
  size and page caps before parsing, content type verified rather than trusted, parsing
  with no network access, stored outside the web root, never served inline.
- **Extraction** — the three tiers above, recording which one was used.
- **Review UI** — every extracted analyte is a *candidate*, shown beside the page it
  came from, requiring confirmation before it becomes a fact. `SAFE-11` covers
  medication fields today; lab values need the same treatment. A misread decimal point
  in a sodium result is not a small error.

**Nothing extracted from a PDF becomes a recorded fact without you confirming it.**
That is not caution for its own sake — OCR misreads digits, and a model will
confidently produce a plausible number.

---

## What else is missing

Grouped by why it matters rather than by phase.

### Security gaps that matter before real data

- **Only a password protects the entire record.** No passkey, no second factor. The
  plan requires one. `hc-cbs.6`.
- **No rate limiting anywhere.** Redis is deployed and completely unused. Login,
  extraction and report generation are all unbounded. `hc-cbs.5`.
- **Integration tokens are not encrypted at rest.** Fine while Telegram's token lives
  in the environment; not fine once Garmin OAuth tokens are in the database. Class C8
  requires keys held outside the database. `hc-cbs.7`.
- **No backups.** The design issue is still open and nothing is implemented. Today a
  disk failure loses everything. `hc-34v.11`, then `hc-cbs.8`.
- **Deletion does not exist.** Export works; you cannot delete a record, an
  integration's data, or your account. `hc-cbs.10`.

### Correctness gaps

- **No extraction evaluation.** Nothing stops a model or prompt change silently
  degrading extraction. Phase 2's acceptance criteria explicitly require a gold set
  with per-field thresholds. `hc-h5r.6`.
- **No draft edit.** A wrong amount can only be cancelled and retyped. The plan
  specifies confirm/**edit**/cancel. `hc-h5r.5`.
- **Draft expiry never runs.** `expire_stale_drafts()` exists and nothing calls it, so
  unanswered drafts keep raw message text indefinitely. `hc-h5r.7`.
- **The job queue is a placeholder.** The worker idles and logs
  `reason_code=job_queue_not_implemented`. Every scheduled thing above needs it.
  `hc-7hu.1`.
- **15 of 29 safety rules are still `pending`.** Mostly UI and report rules that need
  a UI to exist. CI reports the gap on every run.

### The whole user interface

There is no web frontend at all. Six issues (`hc-xo6.*`) cover the foundation, Today,
timeline, plan, charts, and settings. The emergency page is deliberately separate and
already works without any of it.

### Not started, lower urgency

Garmin (gated on its feasibility spike — verify access before building anything),
weather and location enrichment, deterministic analytics, the data-quality page, the
report builder with PDF rendering, monitoring and alerting, accessibility and
performance work.

---

## Suggested order

1. **The three ADRs.** Small, and they change what you build.
2. **Backup design and implementation.** You are accumulating real health data now.
   This is the thing you would most regret skipping.
3. **Job queue.** Several things below need scheduled work.
4. **Labs and PDF.** What you asked for, and independent of the frontend up to the
   review UI.
5. **Frontend foundation, then Today and timeline.** The point at which it becomes an
   application rather than an API.
6. **Telegram long polling.** Makes capture work on your actual hosting.
7. **Security: passkeys, rate limiting, deletion.**
8. **Analytics and reports.** The physician-appointment payoff.
9. **Garmin spike**, then Garmin if the spike says go.
10. **Production checklist, security review, restore drill.**

`bd ready` already reflects this ordering through priorities and dependencies. Trust it
over this list — this list will drift, and Beads will not.
