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
| Bot token | `HC_TELEGRAM_BOT_TOKEN` | `8123456789:AAF...` |
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
  injection - Log an emergency injection
  episode - /episode start <trigger> or /episode end
  today - What's recorded today vs your plan
  undo - Cancel the pending draft
  privacy - What this bot stores
  help - Show help
  ```

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

## Step 3 — Put the two values in `.env`

Edit `.env` in the project root (it is git-ignored — keep it that way):

```bash
HC_TELEGRAM_BOT_TOKEN=8123456789:AAFxx-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
HC_TELEGRAM_ALLOWED_CHAT_ID=123456789
```

## Step 4 — Start the worker

The worker is what talks to Telegram. It holds an outbound connection open, waits for
a message, handles it, and waits again.

```bash
docker compose up -d --force-recreate worker
docker compose logs -f worker
```

You are looking for:

```
telegram poller started   integration=telegram outcome=polling
```

If it says `outcome=idle reason_code=telegram_not_configured`, one of the two values
above is missing or the container did not pick up the new `.env`.

Check the token itself with:

```bash
docker compose run --rm api python -m healthcurve.cli telegram-status
```

You should see `Connected as @your_bot_name`.

## Step 5 — Test it

Message your bot:

| Send | Expect |
|---|---|
| `/help` | The command list |
| `/today` | Today's doses against your approved plan |
| `/dose 15 hydrocortisone` | A draft with **Confirm** / **Cancel** buttons |
| `Took 15mg hydrocortisone at 7:08, slept badly` | A draft listing a dose *and* a symptom |

Press **Confirm** and check the dose appears in the web app's timeline.

Then test that nothing is recorded without you:

- Send `/dose 15 hydrocortisone`, press **Cancel**. Nothing should appear.
- Send `I did not take my morning dose`. It should come back flagged
  *"this reads like a dose you did NOT take"* and refuse to record it.

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
Expected on a fresh install: no model is pulled yet, and this is the designed
fallback rather than a guess. Check with `docker compose exec ollama ollama list`.
Pull one with `docker compose exec ollama ollama pull <model>` and set
`HC_OLLAMA_MODEL` to match. The commands (`/dose`, `/symptom`, `/today`) are
deterministic and keep working without any model at all.

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

## Disconnecting

Stop the worker and remove the `HC_TELEGRAM_*` values from `.env`:

```bash
docker compose stop worker
```

If you had ever used webhook mode, also clear it:

```bash
docker compose run --rm api python -m healthcurve.cli telegram-disconnect
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

Only for a publicly hosted deployment. Set `HC_TELEGRAM_MODE=webhook`, add
`HC_TELEGRAM_WEBHOOK_SECRET` (`openssl rand -base64 32 | tr -d '/+=' | head -c 40`) and
`HC_PUBLIC_BASE_URL`, then:

```bash
docker compose run --rm api python -m healthcurve.cli telegram-register
```

Telegram needs a valid certificate — self-signed, plain HTTP, and `localhost` all fail.
The webhook additionally verifies the secret token in constant time and returns 404
when Telegram is not configured, so an unconfigured bot exposes nothing to probe.
