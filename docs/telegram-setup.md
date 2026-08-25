# Connecting Telegram

Follow these steps in order. About 10 minutes. **Your machine does not need to be
reachable from the internet** -- HealthCurve polls Telegram outbound (ADR-0008), so
this works behind NAT, on a home network, or on a machine reachable only over
Tailscale.

**Before you start, understand what this does.** Messages you send the bot are read by
a language model running on **your own** infrastructure — nothing goes to a third-party
AI service. But Telegram itself stores your chat history on Telegram's servers, and
that is outside HealthCurve's control. If your medication and symptom messages sitting
in a Telegram chat is a problem for you, use the web app instead.

Nothing you send becomes a recorded fact until you press **Confirm**.

---

## What you'll end up with

| Thing | Where it goes | Looks like |
|---|---|---|
| Bot token | Owner-only ignored `.env`, or optional encrypted credential row | never printed |
| Optional encryption keys | Owner-only file outside the repo/database | JSON key ring |
| Your chat ID | `HC_TELEGRAM_ALLOWED_CHAT_ID` | `123456789` |

That is all you need for the default polling mode. No domain, no certificate, no
port forwarding, no tunnel.

The allow-list is not optional: the worker refuses to start without it, because a bot
that answers anyone is a bot anyone can put data into.

> **Webhook mode** (`HC_TELEGRAM_MODE=webhook`) also exists, for a publicly hosted
> deployment. It additionally needs `HC_TELEGRAM_WEBHOOK_SECRET` and
> `HC_PUBLIC_BASE_URL`, and Telegram must be able to reach you over HTTPS. See the
> appendix at the end.

---

## Step 1 — Create the bot

1. Open Telegram and search for **@BotFather** (the one with the blue verified check).
2. Send `/newbot`.
3. It asks for a **name** — the display name. Anything: `HealthCurve`.
4. It asks for a **username** — must be unique and end in `bot`, e.g.
   `jeff_healthcurve_bot`.
5. BotFather replies with a token that looks like:

   ```
   8123456789:AAFxx-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

**That token is a password.** Anyone holding it controls the bot. Do not paste it into
a chat, an issue, or a screenshot. If it leaks, send BotFather `/revoke` immediately.

While you're still in BotFather, harden the bot:

- Send `/setjoingroups` → pick your bot → **Disable**. The bot should never be
  addable to a group; your health data must not end up in one.
- Send `/setprivacy` → pick your bot → **Enable**. Belt and braces on the same point.
- Optionally `/setdescription` and `/setcommands` to make it friendlier. For commands,
  paste:

  ```
  dose - Record a dose: /dose 15 hydrocortisone
  symptom - Record a symptom: /symptom nausea 4
  bp - Record blood pressure: /bp 120/80 62 08:15
  weight - Record body weight: /weight 180 lbs 08:15
  temperature - Record body temperature: /temperature 98.6 F 08:15
  injection - Log an emergency injection
  episode - /episode start <trigger> or /episode end
  today - What's recorded today vs your plan
  location - Explain how to add location to a pending draft
  edit - Correct a pending draft field
  undo - Cancel the pending draft
  privacy - What this bot stores
  help - Show help
  ```

Telegram's BotFather command menu permits underscores but not hyphens. HealthCurve
deliberately uses the owner-requested `/bd-list`, `/bd-status`, and `/bd-add` spellings,
so type those commands directly rather than adding misleading underscore aliases to
the menu.

## Step 2 — Find your chat ID

The bot will only ever respond to this one chat. Everything else is dropped.

1. Open a chat with **your new bot** and send it any message (`hello` is fine).
2. Then, in a terminal on your server, run — substituting your token:

   ```bash
   curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates" \
     | python3 -m json.tool
   ```

3. Look for `"chat": {"id": 123456789, ...}`. **That number is your chat ID.** It's
   usually positive for a private chat.

If `getUpdates` returns an empty `result`, you either haven't messaged the bot yet or a
webhook is already registered. If a webhook is set, `getUpdates` stops working — run
`curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/deleteWebhook"` first, message the
bot, then try again.

## Step 3 — Store the token for the private Mac runtime

For the single-owner localhost/Tailscale deployment, edit the repository-root `.env`
in a private local editor and add:

```dotenv
HC_TELEGRAM_BOT_TOKEN=<token entered only in the private editor>
HC_TELEGRAM_ALLOWED_CHAT_ID=123456789
```

Do not type the token into a shell command, Codex/ChatGPT, Git, Beads, logs, or a
screenshot. Keep the file ignored and owner-only:

```bash
chmod 600 .env
git check-ignore -q .env
```

This is the normal, proportionate boundary for an owner-only Mac. If the Mac becomes
shared with untrusted local users, review this choice.

### Optional defense in depth: encrypted credential row

Database-backed AES-256-GCM storage is available but is not required for the private
runtime. To use it, create an external key once at a path outside the repository:

Create this once at a path outside the repository:

```bash
mkdir -p "$HOME/.config/healthcurve"
uv run python -m healthcurve.cli credential-key-init \
  "$HOME/.config/healthcurve/credential-keys.json" --key-id key_2026_08
```

Keep a separate encrypted copy in your password manager or vault. A database backup
cannot recover provider credentials without this file.

Configure the key mount in `.env` alongside the chat allow-list:

Edit `.env` in the project root (it is git-ignored — keep it that way):

```dotenv
HC_CREDENTIAL_KEY_FILE_HOST=/Users/you/.config/healthcurve/credential-keys.json
HC_TELEGRAM_ALLOWED_CHAT_ID=123456789
```

Now store the token through a hidden prompt. Do not put it on the command line:

```bash
docker compose -f docker-compose.yml -f deploy/credentials.compose.yml run --rm api \
  python -m healthcurve.cli credential-set telegram bot_token
```

The token is AES-256-GCM encrypted before PostgreSQL sees it. The key is supplied by
the read-only mount and never stored in the database. See
[credential-encryption.md](credential-encryption.md) for rotation and recovery.

## Step 4 — Start the worker

The worker is what talks to Telegram. It holds an outbound connection open, waits for
a message, handles it, and waits again.

For the normal private `.env` setup:

```bash
docker compose \
  -f docker-compose.yml \
  -f deploy/backup.compose.yml \
  -f deploy/google-drive-backup.compose.yml \
  up -d --force-recreate api worker
docker compose \
  -f docker-compose.yml \
  -f deploy/backup.compose.yml \
  -f deploy/google-drive-backup.compose.yml \
  ps
```

If you deliberately selected the optional encrypted credential setup, include its
overlay:

```bash
docker compose -f docker-compose.yml -f deploy/credentials.compose.yml \
  up -d --force-recreate api worker
docker compose ps
```

You are looking for:

```
telegram poller started   integration=telegram outcome=polling
```

If it says `outcome=idle reason_code=telegram_not_configured`, one of the two values
above is missing or the container did not pick up the new `.env`.

Check connectivity without printing the token:

```bash
docker compose \
  -f docker-compose.yml \
  -f deploy/backup.compose.yml \
  -f deploy/google-drive-backup.compose.yml \
  run --rm api python -m healthcurve.cli telegram-status
```

You should see `Telegram API connection: verified`. The diagnostic reports only
configured/not-configured state and counts; it does not print the chat ID, bot name,
webhook URL, token, or provider error text.

The same durable worker expires unanswered extraction drafts after six hours and
purges their original message text. It schedules this cleanup every fifteen minutes.
Check the privacy-safe last-run status without displaying any draft content:

```bash
docker compose run --rm api python -m healthcurve.cli draft-expiry-status
```

## Step 5 — Test it

Message your bot:

| Send | Expect |
|---|---|
| `/help` | The command list |
| `/today` | Today's doses against your approved plan |
| `/dose 15 hydrocortisone` | A draft with **Confirm** / **Cancel** buttons |
| `/diary Synthetic note --time=07:30 --sensitive` | A sensitive diary draft with the entered time |
| `/lifeevent travel Synthetic overnight flight --time=22:15` | A categorized life-event draft |
| `/bd-list` | Queue the fixed host `bd list` operation, then receive its bounded output |
| `/bd-status` | Queue the fixed host `bd status` operation, then receive its bounded output |
| `/bd-add add hydration tracking` | A locally evaluated proposal queue ID, then an existing or new `hc-*` issue ID |
| `Show me bead list` / `Show me bead status` | Queue the same fixed host read without needing the model |
| `Add a Bead for hydration tracking` | Use the same locally evaluated, bounded feature-request queue as `/bd-add` |
| `Episode starting` / `The episode is over` | Open an episode with an unspecified trigger, or close the current episode |
| `Add a body weight of 179.6 lbs` | A confirmable home weight draft; `lb`, `lbs`, `kg`, and `kgs` are accepted |
| `I just had a symptom of dizziness at 14:30` | A confirmable symptom draft; without a time the confirmation visibly uses message time |
| `Took 15mg hydrocortisone at 7:08, slept badly` | A draft listing a dose *and* a symptom |

Press **Confirm** and check the dose appears in the web app's timeline.

### Optional phone location

Every draft offers **Add location (optional)**. It works only in the allow-listed
private chat. Choosing it opens three deliberate choices:

- **Share current location** asks Telegram on the phone for permission. Telegram
  transports the exact coordinate, but HealthCurve validates and rounds it to `0.1°`
  in memory before constructing a database row. Exact GPS never enters PostgreSQL,
  logs, analytics, or HealthCurve backups.
- **Use saved Home area** explicitly reuses the rounded Home coordinate. HealthCurve
  never silently carries a previous location into a new draft.
- **No location** cancels the request and clears any rounded request state.

The request expires after ten minutes and is bound to that pending draft. Confirming
the draft records a separately labeled coarse context fact; cancelling or expiry
clears the request. After sharing a current location, **Save as Home area** stores only
the rounded coordinate. Telegram retains the original location message under the
owner's Telegram settings, so delete it in Telegram if that history is unwanted.

To correct a dose draft, press **Edit** for instructions or send, for example,
`/edit 1 amount 15`. The supported fields are `amount`, `unit`, `time`, and
`medication`. The edited values remain a draft until you press **Confirm**; the
original structured proposal is retained as AI evaluation provenance, while the raw
message text is still purged when the draft is resolved.

Then test that nothing is recorded without you:

- Send `/dose 15 hydrocortisone`, press **Cancel**. Nothing should appear.
- Send `I did not take my morning dose`. It should come back flagged
  *"this reads like a dose you did NOT take"* and refuse to record it.

### Short-lived clarification memory

When the bot asks a clarification about a product request, the question, original
request, and bounded recent exchange are stored in the `ai` working-data namespace.
The next short reply in the same owner/chat can answer that question even after the
worker restarts. This does not give general chat history authority over deterministic
commands, recorded facts, emergency information, or physician-approved plans.

The default window expires after three hours and retains at most 12 turns or 8,000
characters, whichever is smaller. `HC_TELEGRAM_CONTEXT_TTL_MINUTES`,
`HC_TELEGRAM_CONTEXT_MAX_TURNS`, and `HC_TELEGRAM_CONTEXT_MAX_CHARS` can reduce or
increase those bounds within the configured safety limits. Expired or malformed rows
are deleted on read and during ordinary draft-expiry cleanup. `cancel`, `never mind`,
`/undo`, and a new explicit command clear or supersede the pending exchange. Context
is isolated by owner and Telegram chat, is not logged, and is removed with the AI
working data when the account is deleted. Telegram's own chat-history retention is
separate and remains under Telegram's controls.

---

## Troubleshooting

**Bot doesn't reply at all.**
Check the worker is running and polling: `docker compose logs worker | tail`. If it is
idle, the token or chat id is missing. If it logs `telegram poll failed` repeatedly,
your machine cannot reach `api.telegram.org` — that is a network or DNS problem on your
side, and the poller will keep retrying with backoff until it can.

**Replies are a few seconds behind.**
Expected. The poller holds a 25-second connection open; a message arriving mid-window
is picked up immediately, but a reply can lag slightly. This is the trade for needing
no public endpoint.

**Nothing arrives while the machine is asleep.**
Also expected, and the main practical cost of polling. Telegram holds updates for about
24 hours, so messages sent while the laptop was closed arrive once the worker is back.

**Bot replies to `/help` but says "the language model is unavailable" for plain
sentences.**
The safe fallback means the configured model cannot be reached. HealthCurve defaults
to ADR-0010's `qwen3:30b`. With native Ollama on macOS, run `ollama list` and
`ollama pull qwen3:30b`; with the bundled CPU-only service, use
`docker compose exec ollama ollama list` and
`docker compose exec ollama ollama pull qwen3:30b`. Confirm `HC_OLLAMA_MODEL` names the
installed tag. The recording commands (`/dose`, `/symptom`, `/bp`, `/weight`,
`/temperature`, `/today`) and fixed read commands (`/bd-list`, `/bd-status`) keep
working without any model. `/bd-add` remains available as a safe entry point but
creates nothing until the local model can validate a proposal.

**`telegram update failed` with a `reason_code`.**
The `reason_code` is the exception type. `ProgrammingError` is usually a database
permission problem; `ConnectError` and `ReadTimeout` are network. The message itself
is deliberately not logged — httpx errors embed the URL, which contains the bot token,
and database errors echo bound parameters, which contain health values.

**"I don't know 'x'" when naming a medication.**
The name must match one you loaded. List them with `/today`, or add the medication in
the web app. The bot deliberately will not guess at a medication it doesn't recognise.

**"Conflict: terminated by other getUpdates request".**
Two pollers are running against one bot token. Stop the duplicate — usually a second
`docker compose up` or a stray local process.

**You previously set a webhook.**
The poller deletes it automatically on startup, keeping any queued messages. Telegram
refuses polling while a webhook is registered, so this has to happen.

**Messages from someone else.**
They're dropped and logged, never processed. If it keeps happening, your bot username
has been found by a scanner; that's expected and harmless, but you can rotate the bot
with BotFather's `/revoke` if you'd rather.

---

## Message time after an outage

Telegram includes the original send timestamp with each queued message. HealthCurve
uses that validated timestamp as the reference time for commands and free-text health
records when the message does not state a time, so a message processed after a local
power or server outage keeps the time it was sent. An explicit time in the message
remains authoritative. Missing, malformed, or future provider timestamps safely fall
back to the local processing time; the update record preserves which source was used.

---

## Disconnecting

Stop the worker. If webhook mode was ever used, clear the remote webhook while the bot
token still exists. Then destroy the encrypted secrets and remove the chat ID from
`.env`:

```bash
docker compose stop worker
docker compose -f docker-compose.yml -f deploy/credentials.compose.yml run --rm api \
  python -m healthcurve.cli telegram-disconnect
docker compose -f docker-compose.yml -f deploy/credentials.compose.yml run --rm api \
  python -m healthcurve.cli credential-delete telegram bot_token
docker compose -f docker-compose.yml -f deploy/credentials.compose.yml run --rm api \
  python -m healthcurve.cli credential-delete telegram webhook_secret
```

To retire the bot entirely, send BotFather `/deletebot`.

Facts you already confirmed stay in your record — they're yours, and they were recorded
by you, not by the integration.

---

## What actually protects you

Both transports funnel into the same code
([dispatch.py](../src/healthcurve/integrations/telegram/dispatch.py)), so the checks
are identical either way:

1. **Chat ID must match the allow-list.** Anything else is counted and dropped without
   being processed.
2. **Update ID must be new.** A redelivery after a crash is a no-op rather than a
   duplicate record.
3. **Payload size capped**, and non-text messages rejected before any model call.

In polling mode there is no inbound endpoint at all, so the whole class of forged-
webhook attacks (threat model T4) simply does not apply — there is nothing to forge a
request *to*.

And the backstop that matters most: an inbound message can only ever create a
**draft**. Turning a draft into a recorded fact needs you to press Confirm
(`SAFE-11`, `SAFE-12`), and the AI database role has no write access to your facts or
your plan at all (`SAFE-15`, `SAFE-16`).

---

## Appendix: webhook mode

Only for a publicly hosted deployment. Tailscale-only production does not use this.
Set `HC_TELEGRAM_MODE=webhook` and `HC_PUBLIC_BASE_URL`, then store a generated secret
through `credential-set telegram webhook_secret` rather than `.env`:

```bash
docker compose -f docker-compose.yml -f deploy/credentials.compose.yml run --rm api \
  python -m healthcurve.cli telegram-register
```

Telegram needs a valid certificate — self-signed, plain HTTP, and `localhost` all fail.
The webhook additionally verifies the secret token in constant time and returns 404
when Telegram is not configured, so an unconfigured bot exposes nothing to probe.
