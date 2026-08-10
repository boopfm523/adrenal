# Telegram feature-request bridge

`/beads-add` lets the single allowlisted Telegram chat add a bounded product idea
to the `hc-inbox` Beads epic. It does not start Codex, claim work, run commands from
the message, or change existing issues.

Example:

```text
/beads-add add a feature that allows me to record hydration
```

The worker writes a small request envelope to `var/beads-outbox/pending`. The worker
has no repository mount and no `bd` binary. A trusted host process reads the envelope,
invokes the installed `bd` CLI with a fixed argument list (never a shell), syncs the
Beads database, and sends the new `hc-*` ID back to the configured Telegram chat.

## Configuration

In `.env`:

```dotenv
HC_BEADS_OUTBOX_DIR=./var/beads-outbox
HC_BEADS_BACKLOG_EPIC_ID=hc-inbox
```

The existing `HC_TELEGRAM_BOT_TOKEN` and `HC_TELEGRAM_ALLOWED_CHAT_ID` are used by
the host bridge. Never put those values in Beads, logs, command arguments, or this
document.

Run one drain manually from the repository root:

```bash
uv run python scripts/beads_feature_bridge.py --once --repo /Users/jeff/Documents/adrenal
```

Run continuously:

```bash
uv run python scripts/beads_feature_bridge.py --repo /Users/jeff/Documents/adrenal
```

For unattended operation on macOS, install the repository's LaunchAgent template
after replacing its two `REPOSITORY_PATH` placeholders, then use `launchctl bootstrap`
for the current GUI user. Its working directory must be the repository so `Settings`
can read `.env` and `bd` can find the correct Beads database.

## Safety and failure behavior

- Only Telegram's configured allowlisted chat reaches command dispatch.
- Requests are limited to 500 characters. Obvious credentials, contact details, and
  personal measurement values are rejected. Describe the feature, not your data.
- Quotes, newlines, semicolons, `$()`, and leading dashes remain literal argv values.
- Fixed fields are type `feature`, priority `P2`, labels `source:telegram` and
  `area:product`, and parent `hc-inbox`. The trusted host independently checks the
  configured parent and rejects any worker envelope that tries to select another.
- The Telegram message ID becomes a one-way hashed idempotency key. A result record
  prevents a Telegram delivery retry from creating another issue.
- A matching Beads `external_ref` recovers safely if the bridge crashes after create.
- A failed Beads call, Dolt push, or Telegram acknowledgement leaves the request in
  `pending` and emits only a reason code. The request text and credentials are not
  logged.
- Later agents still pull, claim, implement, test, close, sync, commit, and push using
  the root `AGENTS.md`. The request cannot bypass that workflow.
