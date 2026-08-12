# Telegram Beads bridge

The single allowlisted Telegram chat can use `/bd-list`, `/bd-status`, and `/bd-add`
without giving the application container repository or shell access. `/beads-add`
remains a documented compatibility alias for `/bd-add`. These commands do not start
Codex, claim work, run message text as a command, or change an existing issue other
than the bounded new inbox Bead deliberately requested through `/bd-add`.

`/bd-list` and `/bd-status` write an envelope containing only the fixed enum `list` or
`status`. The trusted host bridge maps those values to exact argument vectors
`[bd, list]` and `[bd, status]`, never a shell. It strips terminal control sequences,
redacts token-shaped output, caps the Telegram reply, and reuses a durable result if
delivery must retry. The bot first acknowledges the queue ID; the host bridge then
returns the command output to the same allowlisted chat.

Natural phrases such as “show me the current bd list,” “what is the Beads status,” or
“add a Bead for hydration tracking” first pass through a separate local Ollama intent
schema whose only values are `list`, `status`, `add`, and `none`. `list` and `status`
queue the same fixed envelopes. `add` still passes through the full feature-proposal
safety evaluation below. If Ollama is unavailable or invalid, no operation is guessed;
the reply points to the deterministic slash commands.

`/bd-add` lets the owner propose a bounded product idea for the `hc-inbox` Beads epic.

Example:

```text
/bd-add add a feature that lets me record hydration and review a daily total
```

The response is intentionally two-stage:

1. The Telegram worker says that the request was evaluated locally and gives a
   one-way `tg-*` queue ID.
2. The host bridge searches open and closed Beads for a strong duplicate. It then
   replies with either the existing `hc-*` ID or the newly created one.

The Bead still waits for a later agent to pull and claim it normally. Sending the
command never authorizes implementation.

## Local-model evaluation boundary

For `/bd-add`, the worker sends only the request text to the configured local
Ollama/Qwen endpoint.
There is no cloud fallback. Ollama receives the text as an untrusted JSON value, under
a versioned system prompt and strict JSON Schema. It can return either:

- a generated title, problem statement, design constraints, testable acceptance
  criteria, allowlisted area/risk labels, and duplicate-search terms; or
- one clarification question when the outcome is ambiguous or needs a medical-safety,
  privacy, or security decision.

Every generated field is then validated independently for schema, size, control
characters, fixed label allowlists, secrets, personal measurements, contact details,
and exact coordinates. Prompt-injection phrases are rejected before the model call.
Requests to diagnose, prescribe, or automatically change medication receive a safe
clarification instead of a Bead.

If Ollama is unavailable, times out, omits its model identity, returns malformed JSON,
adds unsupported fields, copies the raw directive, or produces unsafe content,
HealthCurve creates nothing. The bot gives a retry/rephrase response; it never falls
back to inserting the Telegram directive verbatim.

## Outbox and host boundary

After successful evaluation, the worker writes a versioned envelope to
`var/beads-outbox/pending`. The envelope contains only:

- the generated proposal;
- a one-way hash derived from the Telegram message ID;
- the fixed inbox parent; and
- local model tag/digest plus prompt and schema versions.

It does **not** contain the raw Telegram request, chat ID, bot token, health record, or
credentials. The worker has no repository mount and no `bd` binary.

A trusted host process independently validates the complete envelope, invokes the
installed `bd` CLI with a fixed argv list (never a shell), searches all Beads for the
hashed external reference and strong content duplicates, syncs the Beads database,
and sends the resulting `hc-*` ID to the configured Telegram chat. The model cannot
choose priority, status, assignee, parent, dependencies, arbitrary labels, commands,
or implementation actions.

## Configuration

In `.env`:

```dotenv
HC_BEADS_OUTBOX_DIR=./var/beads-outbox
HC_BEADS_BACKLOG_EPIC_ID=hc-inbox
HC_OLLAMA_BASE_URL=http://host.docker.internal:11434
HC_OLLAMA_MODEL=qwen3:30b
```

The existing encrypted/configured Telegram credentials are used by the host bridge.
Never put their values in Beads, logs, command arguments, or documentation.

Run one drain manually from the repository root:

```bash
uv run python scripts/beads_feature_bridge.py --once --repo /Users/jeff/Documents/adrenal
```

Run continuously:

```bash
uv run python scripts/beads_feature_bridge.py --repo /Users/jeff/Documents/adrenal
```

The continuous bridge watches its loaded bridge and operation-schema source files.
When either changes, it exits cleanly so the macOS `KeepAlive` LaunchAgent immediately
starts a fresh process with the current code. Permanently invalid or incompatible
envelopes are moved to `var/beads-outbox/failed` after a generic Telegram failure
notice is delivered; they are not retried forever.

For unattended operation on macOS, install the repository's LaunchAgent template
after replacing its two `REPOSITORY_PATH` placeholders, then use `launchctl bootstrap`
for the current GUI user. Its working directory must be the repository so `Settings`
can read `.env` and `bd` can find the correct Beads database.

Inspect the unattended bridge without exposing Telegram credentials or health data:

```bash
launchctl print gui/$(id -u)/com.healthcurve.beads-feature-bridge
find var/beads-outbox/pending -maxdepth 1 -name 'tg-*.json' -print
tail -50 /tmp/healthcurve-beads-feature-bridge.error.log
```

A fixed read request should normally leave `pending` within the bridge's 10-second
poll interval. If the LaunchAgent predates the self-reload behavior or is otherwise
stale, reload only this service:

```bash
launchctl kickstart -k gui/$(id -u)/com.healthcurve.beads-feature-bridge
```

## Recovery behavior

- An identical Telegram update is claimed once by the Telegram update ledger. The
  hashed message ID also makes the outbox idempotent before another model call.
- A matching Beads external reference recovers a crash that happened after creation.
- A durable result file prevents failed Telegram acknowledgement from creating a
  second issue on retry.
- A strong title/content match returns the existing open or closed Bead instead of
  creating a duplicate.
- A failed Beads call, Dolt push, or Telegram acknowledgement keeps the request in
  `pending` and logs only a reason code—never proposal text or credentials.
- An old, malformed, or tampered envelope fails closed. Correct the local service and
  submit the feature again; do not hand-edit an envelope into validity.

Later agents still pull, claim, implement, test, close, sync, commit, and push using
the repository-root `AGENTS.md` and normal Beads workflow.
