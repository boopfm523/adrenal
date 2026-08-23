# Garmin Connect automatic sync

HealthCurve can read a deliberately small set of personal Garmin Connect data through
the owner-selected
[`python-garminconnect`](https://github.com/cyberjunky/python-garminconnect) client.
This is an unofficial client of Garmin's private web services, not Garmin's supported
Health API. Garmin can change or block it without notice. Reviewed FIT/CSV/ZIP import
remains the durable fallback described in [garmin-import.md](garmin-import.md).

The integration is read-only. HealthCurve's adapter exposes only daily statistics,
sleep, activity-list, heart-rate, stress, respiration, HRV, and step reads; it cannot
create, update, or delete anything in the Garmin account. HealthCurve is not
interpreting these observations as diagnoses, causes, adrenal conclusions, or
medication advice.

## Data included in the first release

HealthCurve stores only these fields when Garmin supplies them:

- workouts: provider activity ID, activity type, experienced start time and timezone,
  elapsed duration, and optional distance converted deterministically to miles;
- daily steps, resting heart rate in bpm, and average Garmin stress score;
- provider-defined aggregate facts for nightly-average HRV; waking- and sleeping-
  period average respiration; and daily low/high respiration;
- timestamped heart rate, stress, respiration, and nightly HRV samples when available;
- hourly step totals produced by summing only Garmin's observed intraday step buckets;
- sleep start, wake time, duration, duration source, number of awakenings, sleep
  score, and explicitly bounded awake-stage intervals when Garmin supplies them.

Missing stays missing. HealthCurve never substitutes zero. Intensity minutes, Body
Battery, Pulse Ox, calories, Garmin weight, non-awake sleep-stage detail, GPS routes,
and every other provider field are deferred unless a later Bead explicitly adds them.

The selected-day sleep endpoint uses interval overlap, so a session beginning before
midnight still appears on its wake day. HealthCurve stores only explicit bounded awake
intervals under the immutable sleep revision. An aggregate awakening count never
becomes guessed timestamps; when the provider reports awakenings without their timing,
the graph says that intermediate timing is unavailable. See
[ADR-0016](adr/0016-garmin-sleep-interval-contract.md).

## Verified intraday and aggregate contract

The isolated client can read timestamped heart rate, stress, respiration, nightly HRV,
and bounded intraday step intervals. Privacy-safe configured-account probes verified
predominant sample spacing of 2, 3, 2, and 5 minutes for the first four series and
15-minute step buckets in the observed day; gaps are expected and cadence is not
guaranteed.
Heart rate is bpm, respiration is breaths/min, HRV is ms, and stress is Garmin's 0–100
score. Negative stress/respiration sentinels and null values mean missing; stress zero
is a valid reading.

HealthCurve deterministically sums valid step buckets into local-clock hours for the
selected day. An observed zero remains zero, while an absent or malformed bucket stays
missing. The resulting hourly series is optional on the daily HealthCurve and its
tooltip retains the exact step total; the daily step aggregate remains a separate
untimed fact.

The same responses expose a provider `lastNightAvg` HRV value and separate waking and
sleeping respiration averages plus daily respiration low/high. HealthCurve records
each with its exact provider field and period label. The client does **not** expose a
distinct all-day HRV average: capability status reports that value as unsupported,
rather than relabeling Garmin's weekly average or calculating a substitute. Aggregate
facts are visible as untimed daily/nightly context and in exact-value tables, never as
invented midnight samples or intraday curves.

Garmin supplies no stable sample ID, revision token, or per-sample IANA timezone for
these responses. [ADR-0014](adr/0014-garmin-intraday-read-contract.md) therefore defines
deterministic metric-plus-UTC-timestamp identities, content revisions, timezone
handling, bounded parsing, and separation between timestamped readings and daily or
nightly aggregates. Re-reading the same provider field is idempotent; a changed value
creates a provider correction while retaining provenance.

## Security boundary

- The one-time connector receives the Garmin email, password, and hidden MFA prompt.
- The scheduled Garmin worker receives only PostgreSQL access and the Garmin token
  directory. It receives no Telegram token, credential key ring, Ollama connection,
  documents, report storage, or Beads access.
- The API receives only the non-secret enable flag and lookback length. It cannot read
  the Garmin token directory.
- The token directory must be absolute, outside the repository, mode `0700`; the token
  file must be mode `0600`. Tokens are password-equivalent and are deliberately
  excluded from HealthCurve exports and backups.
- Provider responses are mapped in memory to the allow-listed fields. Raw responses,
  provider exception text, credentials, and MFA codes are neither persisted nor
  logged.

## First connection

These commands are local operator actions. Do not paste credentials into a command,
commit, Bead, screenshot, or chat.

1. Create the external token directory:

   ```bash
   mkdir -p "$HOME/.config/healthcurve/garmin"
   chmod 700 "$HOME/.config/healthcurve/garmin"
   ```

2. In the ignored `.env`, temporarily set:

   ```dotenv
   HC_GARMIN_ENABLED=true
   HC_GARMIN_EMAIL=your-garmin-login-email
   HC_GARMIN_PASSWORD=your-garmin-password
   HC_GARMIN_TOKEN_STORE_HOST=$HOME/.config/healthcurve/garmin
   HC_GARMIN_SYNC_LOOKBACK_DAYS=3
   ```

3. Build the code and apply the migration before connecting:

   ```bash
   docker compose build api
   docker compose run --rm api alembic upgrade head
   ```

4. Run the one-time interactive connector:

   ```bash
   docker compose -f docker-compose.yml -f deploy/garmin.compose.yml \
     --profile garmin-connect run --rm garmin-connect
   ```

   If Garmin asks for MFA, enter the code only at the hidden terminal prompt. A
   successful run prints a small JSON status containing no account identity or token.

5. Immediately remove the values of `HC_GARMIN_EMAIL` and `HC_GARMIN_PASSWORD` from
   `.env`. Leave the enable flag, host token path, and lookback configured.

   Do not add empty placeholders for the removed credentials. The scheduled worker
   starts from the protected token store alone. If the one-time connector is run
   again without both real credentials, it exits with
   `garmin_credentials_not_configured` and does not access the database or Garmin.

6. Start or rebuild the API, web frontend, and isolated worker through one rendered
   topology:

   ```bash
   docker compose -f docker-compose.yml -f deploy/garmin.compose.yml \
     --profile garmin up -d --build api caddy garmin-worker
   ```

   Use that same base-plus-overlay prefix for routine Garmin operations:

   ```bash
   docker compose -f docker-compose.yml -f deploy/garmin.compose.yml \
     --profile garmin restart garmin-worker
   docker compose -f docker-compose.yml -f deploy/garmin.compose.yml \
     --profile garmin ps garmin-worker
   ```

   PostgreSQL's unpublished `hc-garmin` network membership is deliberately part of
   the base Compose topology. This means an ordinary base-file update such as
   `docker compose up -d caddy` cannot remove the database path from an already-running
   Garmin worker. The overlay adds only the opt-in worker and its protected token
   mount; it no longer owns the database network attachment.

The Settings page shows safe connection state, last success, capability availability,
latest warning codes, a manual-sync button, and an impact preview. Health data shows a
compact table in experienced-time order with Garmin attribution and explicit
missingness. The Timeline, export, data-quality page, and wearable report section use
the same current recorded facts.

## Scheduling and reconciliation

The worker queues one bounded window per owner and local calendar day at or after the
configured owner-local hour (`HC_GARMIN_SYNC_HOUR_LOCAL`, 09:00 by default), with a
maximum of 31 days per job. This gives the watch and phone time to finish their own
Garmin Connect sync; manual HealthCurve sync remains available before that hour. The
owner-selected lookback defaults to three days and can be changed from 1 to 31 days on
Settings. Both automatic sync and **Sync Garmin now** use that saved window. This lets
an owner temporarily select seven days for a catch-up sync and then restore three days
for ordinary operation. Every scheduled window re-reads the full saved lookback, so
late or corrected Garmin values within that window are still detected. Reads are
rate-spaced and durable jobs have bounded retries. Stable provider
identifiers and revision hashes make overlapping windows idempotent. If Garmin later
changes a value, HealthCurve creates a correction linked to the original row; it never
silently rewrites history.

Equivalent work is coalesced by exact owner, timezone, start date, and end date. A
manual click, a newly generated browser request key, and worker scheduler startup all
reuse one queued or running durable job for that exact window. A different window,
including the next local day, remains separate and is never discarded. This
coordination is serialized by the owner connection row, so concurrent requests cannot
both create provider reads.

Use **Sync Garmin now** for a bounded manual job. It does not run inside the web
request. After an exact window completes, ordinary manual requests reuse that result
for 30 minutes rather than reading Garmin again. Settings reports whether a request
was newly queued, shared with active work, or suppressed by this cooldown and shows
the requested dates. **Refresh recent Garmin window** deliberately bypasses only the
completed-window cooldown; it still coalesces with equivalent queued or running work.
Future-dated and over-31-day windows are rejected. A queued sync that races with
disconnect becomes a no-op, so Garmin is not contacted after consent is withdrawn.

Each completed sync also keeps its request origin: **Scheduled automatic sync**,
**Manual sync**, or **Manual refresh**. Rows created before origin tracking are
explicitly labeled **Origin unavailable (older sync)**. Settings shows the latest
completed origin, and completed-warning notices on Data quality include it. Origin is
provenance, not a reason to duplicate work: equivalent active windows are still shared
and ordinary completed-window cooldown still applies across request origins.

## Reauthentication and failures

Safe status codes such as `garmin_authentication_required`, `garmin_mfa_required`,
`garmin_rate_limited`, `garmin_response_shape_changed`, and
`garmin_provider_unavailable` may appear in status or operational logs. They contain no
provider exception text.

If reauthentication is required, Settings deliberately disables sync and refresh until
the private session is restored. Existing Garmin-derived facts remain unchanged. On the
HealthCurve host, temporarily restore `HC_GARMIN_EMAIL` and `HC_GARMIN_PASSWORD` in the
ignored `.env`, then run:

```bash
docker compose -f docker-compose.yml -f deploy/garmin.compose.yml \
  --profile garmin-connect run --rm garmin-connect
```

Complete MFA at the hidden prompt if requested, remove the email/password values again,
and use **Check Garmin connection again** in Settings. Do not use the disconnect control
to repair authentication because disconnect is a separate destructive workflow. If the
unofficial client stops working, disable
`HC_GARMIN_ENABLED`, stop `garmin-worker`, and use reviewed export import. Existing
recorded facts remain available unless explicitly deleted.

## Disconnect and deletion

Settings shows the exact number of automatic facts, reviewed-import facts, and sync
provenance rows before any action. Re-enter the HealthCurve password and type one exact
phrase:

- `DISCONNECT GARMIN` stops future sync and removes the local token while retaining
  recorded Garmin facts.
- `DISCONNECT GARMIN AND DELETE DATA` also deletes Garmin-derived correction chains,
  reviewed source batches, and automatic sync provenance.

The database decision and token-removal job are atomic and retryable. The unofficial
client cannot guarantee Garmin-side revocation, so after disconnect also review and
end relevant sessions in Garmin account security. Encrypted HealthCurve backups can
retain deleted database rows until their normal retention expiry.

## Operational checks

When automatic sync is expected, set `HC_MONITOR_GARMIN_AGE_LIMIT_H` to the accepted
maximum age. Monitoring uses the newest successful automatic sync or reviewed import
and reports only age/reason codes. `garmin_sync_stopped` means the enabled automatic
connection is stale; it does not mean a health value was zero.

Never use a real account in CI. Synthetic tests cover mapping, missingness, DST/travel,
miles conversion, idempotency, correction history, rate spacing, malformed responses,
owner scoping, export, reports, deletion, and token protections.
