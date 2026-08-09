# Connecting Telegram

Follow these steps in order. The whole thing takes about 15 minutes, and step 5 is the
only one that needs your server to be reachable from the internet.

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
| Webhook secret | `HC_TELEGRAM_WEBHOOK_SECRET` | random string you generate |
| Your chat ID | `HC_TELEGRAM_ALLOWED_CHAT_ID` | `123456789` |
| Your public URL | `HC_PUBLIC_BASE_URL` | `https://health.example.com` |

All four are required. HealthCurve refuses to run the bot with any of them missing —
a bot without a webhook secret and a chat allow-list is one anyone can write to.

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

## Step 3 — Generate a webhook secret

This is what proves an incoming request really came from Telegram. Generate a fresh
random one — do not reuse a password:

```bash
openssl rand -base64 32 | tr -d '/+=' | head -c 40
```

Copy the output.

## Step 4 — Put the four values in `.env`

Edit `.env` in the project root (it is git-ignored — keep it that way):

```bash
HC_TELEGRAM_BOT_TOKEN=8123456789:AAFxx-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
HC_TELEGRAM_WEBHOOK_SECRET=<the string from step 3>
HC_TELEGRAM_ALLOWED_CHAT_ID=123456789
HC_PUBLIC_BASE_URL=https://health.example.com
```

Then restart so the app picks them up:

```bash
docker compose up -d --force-recreate api
```

Check it took:

```bash
docker compose run --rm api python -m healthcurve.cli telegram-status
```

You should see `Connected as @your_bot_name`. If a value is missing it will say which.

## Step 5 — Register the webhook

**Telegram requires a public HTTPS URL with a valid certificate.** Self-signed will not
work, and neither will plain HTTP or `localhost`. Your server must be reachable from
the internet at `HC_PUBLIC_BASE_URL`, with Caddy holding a real certificate.

```bash
docker compose run --rm api python -m healthcurve.cli telegram-register
```

This points Telegram at `https://your.domain/api/v1/integrations/telegram/webhook` and
registers your secret with them.

Verify:

```bash
docker compose run --rm api python -m healthcurve.cli telegram-status
```

`Webhook URL` should show your URL and `Pending updates` should be `0`.

### If you're not publicly hosted yet

You can test locally with a tunnel. Only do this with **synthetic data** — a tunnel
puts your API on the public internet.

```bash
# in one terminal
cloudflared tunnel --url http://localhost:8080     # or: ngrok http 8080
# then, with the https URL it prints:
docker compose run --rm api python -m healthcurve.cli \
    telegram-register --base-url https://something.trycloudflare.com
```

Tear it down afterwards with `telegram-disconnect`.

## Step 6 — Test it

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
Check `telegram-status`. If `last_error_message` mentions SSL or a connection problem,
Telegram can't reach you — your domain, certificate, or firewall is the issue, not
HealthCurve. Confirm from outside: `curl -I https://your.domain/health/live`.

**Bot replies to `/help` but ignores plain sentences.**
That's the language model being unreachable, and it's the designed fallback. Check
`docker compose logs ollama` and confirm the model is pulled:
`docker compose exec ollama ollama list`. Pull it if not:
`docker compose exec ollama ollama pull qwen3-coder`.

**"I don't know 'x'" when naming a medication.**
The name must match one you loaded. List them with `/today`, or add the medication in
the web app. The bot deliberately will not guess at a medication it doesn't recognise.

**Telegram says the webhook returns 403.**
Your `HC_TELEGRAM_WEBHOOK_SECRET` in `.env` no longer matches what Telegram holds. Run
`telegram-register` again.

**Telegram says 404.**
The app doesn't consider Telegram configured — one of the three settings is missing.
Run `telegram-status`.

**Messages from someone else.**
They're dropped and logged, never processed. If it keeps happening, your bot username
has been found by a scanner; that's expected and harmless, but you can rotate the bot
with BotFather's `/revoke` if you'd rather.

---

## Disconnecting

```bash
docker compose run --rm api python -m healthcurve.cli telegram-disconnect
```

Then remove the four `HC_TELEGRAM_*` / `HC_PUBLIC_BASE_URL` values from `.env` and
restart. To retire the bot entirely, send BotFather `/deletebot`.

Facts you already confirmed stay in your record — they're yours, and they were recorded
by you, not by the integration.

---

## What the webhook actually enforces

For reference, in the order the checks run
([telegram.py](../src/healthcurve/api/routers/telegram.py)):

1. **Not configured → 404.** An unconfigured bot exposes no endpoint to probe.
2. **Secret token compared in constant time → 403.** No detail in the response.
3. **Chat ID must match the allow-list.** Anything else is counted and dropped.
4. **Update ID must be new.** Telegram retries until it gets a 200, so replays are
   no-ops rather than duplicate records.
5. **Payload size capped**, and non-text messages rejected before any model call.

And the backstop that matters most: even if all five were bypassed, the webhook can
only ever create a **draft**. Turning a draft into a recorded fact needs you to press
Confirm (`SAFE-11`, `SAFE-12`), and the AI database role has no write access to your
facts or your plan at all (`SAFE-15`, `SAFE-16`).
