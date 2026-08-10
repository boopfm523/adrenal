# HealthCurve Backup Operations Runbook

**Scope:** encrypted local backups, nightly scheduling, retention, integrity status,
and the boundary for an offsite copy. The isolated restore drill is tracked separately
by `hc-cbs.2` and remains a production-launch gate.

## Safety rules

- The backup host stores only an `age` **public recipient**. Keep the private identity
  off the HealthCurve host, out of `.env`, Git, logs, Beads, screenshots, and backups.
- The owner chose a private encrypted-backup directory on the Mac's internal disk plus
  encrypted Google Drive copies and explicitly declined an external drive. This protects
  against database/container failure and provides an offsite copy, but host-disk loss
  can remove both the live database and local set at once; it is not strict 3-2-1.
- The Google Drive OAuth grant can read and delete files created through this rclone
  authorization; Drive offers neither object lock nor a write-only role. The owner
  explicitly accepted that limitation. HealthCurve's adapter exposes no delete or
  replace operation and uses immutable uploads, but it cannot prevent an account or
  token holder from deleting backups outside the application.
- Run tests and drills with synthetic records. Do not paste database output, document
  names, medical content, locations, tokens, or exception text into issues or logs.
- Never restore over the only live copy. Restore into an isolated stack first.

## What runs

The `backup-worker` service is both scheduler and task-restricted consumer:

1. Every poll ensures one `backup.nightly` job exists for the current UTC day.
2. The job is due at 02:00 UTC. Starting after 02:00 causes that day's job to run as
   catch-up; the `(task, idempotency_key)` constraint prevents a duplicate.
3. Only the dedicated worker claims `backup.nightly`. The API and general worker do not
   receive the backup credential, recipient, destination, or source mounts.
4. It creates a PostgreSQL 16 custom dump with the read-only `healthcurve_backup` role,
   validates its inventory, copies uploads/report artifacts and safe restore config,
   writes component checksums, packages the set, and encrypts it with `age`.
5. Plaintext staging is a mode-0700 tmpfs and disappears on process/container exit.
6. The final ciphertext and public envelope are atomically finalized. Retention keeps
   7 daily, 5 ISO-weekly, and 12 monthly sets and never deletes the newest known-good
   set. Invalid/incomplete sets are protected for investigation.
7. A failed attempt is retried with bounded exponential backoff. Exhausted attempts
   become a visible dead letter with a stable reason code, never exception text.

## One-time setup

### 1. Prepare recovery material away from the host

On a separate trusted device with `age` installed, generate an X25519 identity and
record its public recipient. Store the private identity in the owner's password manager
and on separately stored encrypted removable media. Record only the recipient/fingerprint
in HealthCurve configuration.

Confirm that both recovery copies can decrypt a synthetic test file before relying on
the key. If a copy cannot be recovered, the backup is not valid.

### 2. Prepare storage paths

Create three private directories: the local backup destination, uploads source, and
report-artifact source. On Linux the bind-mounted directories must be accessible to
UID/GID `10001:10001`; keep the destination owner-only. On macOS Docker Desktop handles
the UID mapping, but the host directory should still be private to the owner.

`HC_UPLOADS_DIR` is also mounted read/write into the API and the no-network document
worker. It contains retained source medical PDFs plus opaque validation state, so it
must not be placed under the web root or inside Git. The backup worker mounts that same
host directory read-only and includes it in every encrypted backup set.

Do not use the PostgreSQL data volume, `/tmp`, or an unencrypted shared folder as the
destination. This personal deployment uses the repository-ignored `var/backups`
directory by owner choice; it is private but shares the Mac's failure domain.

### 3. Configure `.env`

Set these locally; never commit the values:

```dotenv
POSTGRES_BACKUP_PASSWORD=<random dedicated credential>
HC_BACKUP_AGE_RECIPIENT=<age public recipient>
HC_BACKUP_LOCAL_DIR=<absolute dedicated backup path>
HC_UPLOADS_DIR=<absolute uploads path>
HC_REPORT_ARTIFACTS_DIR=<absolute report-artifacts path>
HC_BACKUP_RETENTION_APPLY=true
```

`HC_BACKUP_RETENTION_APPLY=false` is a safe rollout mode: backups run and retention is
planned, but no old verified local set is removed.

### 4. Provision the read-only database role

For a new PostgreSQL volume, setting `POSTGRES_BACKUP_PASSWORD` before first startup
runs `deploy/postgres-init/02-backup-role.sh` automatically.

For an existing volume, restart PostgreSQL with the variable configured and run the
idempotent script once:

```bash
docker compose exec \
  -e POSTGRES_BACKUP_PASSWORD="$POSTGRES_BACKUP_PASSWORD" \
  postgres /docker-entrypoint-initdb.d/02-backup-role.sh
```

The integration suite verifies that this role can read all durable schemas and cannot
insert into them. Do not replace it with the owner or superuser credential.

### 5. Apply migrations and start scheduling

```bash
docker compose run --rm api alembic upgrade head

docker compose \
  -f docker-compose.yml \
  -f deploy/backup.compose.yml \
  --profile backup-scheduled \
  up -d --build backup-worker
```

Starting the service schedules the current UTC day's singleton job. The container's
restart policy keeps the scheduler running after ordinary reboots.

### 6. Activate the owner-approved Google Drive copy

Install `rclone`, create a private remote using OAuth scope `drive.file`, and keep its
configuration outside the repository at mode `0600`. That scope limits this grant to
files it creates or the user explicitly opens with it; it does not make the credential
write-only. Never paste the config, refresh token, client secret, or account identifier
into `.env`, Git, logs, screenshots, or Beads.

The host-specific setting points Compose to that external file:

```dotenv
HC_RCLONE_CONFIG_FILE_HOST=<absolute path to the mode-0600 rclone config>
```

Start the worker with both overlays:

```bash
docker compose \
  -f docker-compose.yml \
  -f deploy/backup.compose.yml \
  -f deploy/google-drive-backup.compose.yml \
  --profile backup-scheduled \
  up -d --build backup-worker
```

The provider overlay supplies the registered `rclone-google-drive` adapter and the
private destination `HealthCurve Backups`. It mounts the config read-only only into
the dedicated backup worker. The API, Telegram worker, document worker, and general
job worker do not receive the OAuth token or backup recipient.

### Replace rclone's retiring shared OAuth client

Rclone 1.75 reports that its shared Google Drive client will stop working during 2026.
`hc-cbs.8.5` is therefore a P0 production gate. Use an owner-controlled **Desktop app**
OAuth client and keep the existing least-privilege `drive.file` scope. Do not broaden it
to full Drive access merely because rclone's general-purpose guide demonstrates that
scope.

The Google Cloud and browser authorization steps are owner actions. Sign in to Google
Cloud as the account that will own the OAuth project; the Drive authorization itself
must be completed as the account that owns `HealthCurve Backups`.

1. In [Google Cloud Console](https://console.cloud.google.com/), create or select a
   project dedicated to HealthCurve backups.
2. In **APIs & Services**, enable **Google Drive API**.
3. In **Google Auth Platform**, configure Branding with an accurate private-use app
   name, owner support email, and owner contact email. Set Audience to **External**
   unless the account is part of a Google Workspace organization and the Drive account
   is inside that same organization.
4. Under Data Access, add only
   `https://www.googleapis.com/auth/drive.file`. Do not add `drive`, `docs`, or a
   service-account grant.
5. Add the Drive owner as a test user while configuring the client. Before relying on
   unattended backups, publish the personal-use app to **In production**: Google's
   Testing status expires non-basic refresh-token grants after seven days. A personal
   app with fewer than 100 users can remain unverified, although Google will show an
   unverified-app warning during authorization.
6. Under Clients, create an OAuth client of type **Desktop app**. Store its client ID
   and client secret in the owner's password manager. Do not paste either value into a
   shell command, Codex/ChatGPT, Beads, Git, screenshots, or `.env`.

Stop the scheduled worker before changing the grant:

```bash
cd /Users/jeff/Documents/adrenal
docker compose \
  -f docker-compose.yml \
  -f deploy/backup.compose.yml \
  -f deploy/google-drive-backup.compose.yml \
  --profile backup-scheduled \
  stop backup-worker
```

Edit the existing remote interactively so the secret is not placed in shell history.
Run this in a private macOS Terminal window outside Codex, VS Code task capture, screen
sharing, or recording: rclone may display a configuration summary while editing.

```bash
rclone --config /Users/jeff/.config/healthcurve/rclone.conf config
```

Choose **Edit existing remote**, then `healthcurve-drive`. Keep storage type `drive`,
enter the private Desktop-app client ID and secret at their prompts, select
`drive.file`, leave the service-account field empty, decline advanced configuration,
and replace the existing token. Allow local browser authorization, sign in as the Drive
owner, approve the requested `drive.file` access, decline Shared Drive configuration,
and keep the edited remote. Do not choose the retiring shared client when prompted.

Run the fixed-output verifier; it reads the private file but never prints its path,
client values, token, account identifier, or parser errors:

```bash
uv run python scripts/check_rclone_drive_config.py \
  --config /Users/jeff/.config/healthcurve/rclone.conf
```

It must report that an owner client, `drive.file` scope, refresh token, and mode-0600
file protections are verified. A failure prints only a stable reason code. Then test
remote access without rendering filenames:

```bash
rclone \
  --config /Users/jeff/.config/healthcurve/rclone.conf \
  --log-level ERROR \
  lsf "healthcurve-drive:HealthCurve Backups" \
  --max-depth 1 >/dev/null
```

Run the purpose-built encrypted synthetic round trip:

```bash
uv run python scripts/verify_rclone_drive_roundtrip.py \
  --config /Users/jeff/.config/healthcurve/rclone.conf
```

The verifier creates a disposable `age` identity in a mode-0700 temporary directory,
encrypts random synthetic content, performs an immutable upload/download, compares the
ciphertext checksum, decrypts and compares the source, then removes all local temporary
material automatically. It never uses or exposes the real backup recovery identity.
The randomly named encrypted probe remains remotely because HealthCurve deliberately
does not grant or exercise deletion capability.

Restart the worker with both provider overlays:

```bash
docker compose \
  -f docker-compose.yml \
  -f deploy/backup.compose.yml \
  -f deploy/google-drive-backup.compose.yml \
  --profile backup-scheduled \
  up -d --force-recreate backup-worker
```

Before closing `hc-cbs.8.5`, complete all of these checks without recording names or
credentials: both verifiers pass; a new `backup.nightly` job completes after the
cutover; its encrypted set and public envelope exist both locally and remotely with
matching metadata; and the backup status command is healthy. Keep the synthetic remote
object as an immutable probe rather than granting or using deletion capability.

If the new authorization fails, keep the worker stopped. Re-run interactive config and
authorize again; do not weaken the scope or expose values for debugging. The live data
and local encrypted sets are unaffected by an OAuth failure. If necessary while the
shared client still functions, re-edit the remote back to the shared client and replace
the token, then repeat the complete cutover later. After a successful cutover, revoke
the old rclone grant in the Google Account's third-party connections and confirm the
next scheduled upload before treating the replacement as complete.

## Verification and monitoring

### Status command

Run inside the scheduled worker:

```bash
docker compose \
  -f docker-compose.yml \
  -f deploy/backup.compose.yml \
  exec backup-worker python -m healthcurve.backup_status
```

The JSON contains only operational fields: state, backup age, latest job state,
redacted reason code, dead-letter flag, protected-set count, and alert reason codes.
It returns `0` when healthy, `2` for an alert state, and `1` when status cannot be
measured. It never emits filenames, database output, health values, or credentials.

The Compose health check runs the same command every 15 minutes. Monitor the container
health state and alert when it becomes unhealthy. Important reason codes are:

- `backup_missing`: no complete checksum-verified local set exists.
- `backup_age_warning`: newest valid set is at least 26 hours old.
- `backup_integrity_failed`: malformed/incomplete envelope or orphan ciphertext found.
- `backup_job_failed`: newest scheduled job exhausted its attempts.
- `backup_status_unavailable`: status could not safely read the database/catalog.

The 26-hour alert threshold provides an intervention window around the 24-hour RPO.
The queue also exposes oldest-due age and dead-letter count through
`healthcurve.operations.jobs.queue_metrics()`.

### Manual encrypted backup

Use this after setup changes and before upgrades:

```bash
docker compose \
  -f docker-compose.yml \
  -f deploy/backup.compose.yml \
  --profile backup-manual \
  run --rm backup
```

Success prints only set ID, ciphertext SHA-256, and byte size. Verify the public
envelope without the private identity:

```bash
docker compose \
  -f docker-compose.yml \
  -f deploy/backup.compose.yml \
  --profile backup-manual \
  run --rm --entrypoint python backup \
  -m healthcurve.operations.backup verify /backup/<set-id>.json
```

### Retention preview

Preview is the default and performs no deletion:

```bash
docker compose \
  -f docker-compose.yml \
  -f deploy/backup.compose.yml \
  --profile backup-manual \
  run --rm --entrypoint python backup \
  -m healthcurve.operations.retention /backup
```

Only after reviewing the set IDs should an operator append `--apply`. Invalid and
incomplete sets are never automatic deletion candidates.

## Offsite copy activation

The code defines and tests a capability-limited offsite writer contract: metadata
`head`, create-if-absent, and post-upload size/SHA-256 verification. Ciphertext is
uploaded before its envelope, existing matching objects make retries idempotent, and a
conflict fails closed. Retained historical sets are backfilled before local cleanup.

Google Drive is the owner-approved initial provider. The `rclone-google-drive` adapter
uploads a checksum sidecar before each ciphertext/envelope, uses immutable `copyto`,
and verifies exact object size plus SHA-256. Sidecar-first ordering makes interrupted
uploads resumable; a conflicting checksum or size fails closed. The adapter has no
delete method.

The normal base overlay remains safely disabled:

```dotenv
HC_BACKUP_OFFSITE_ENABLED=false
```

`deploy/google-drive-backup.compose.yml` enables and supplies the following values;
operators should not duplicate them in `.env`:

```dotenv
HC_BACKUP_OFFSITE_ENABLED=true
HC_BACKUP_OFFSITE_PROVIDER=rclone-google-drive
HC_BACKUP_OFFSITE_DESTINATION=healthcurve-drive:HealthCurve Backups
HC_BACKUP_OFFSITE_CREDENTIAL_FILE=/run/secrets/rclone.conf
```

The worker refuses incomplete configuration, empty/linked/group-readable credential
files, a maintenance credential in its environment, a missing adapter, upload conflict,
or remote metadata mismatch. Google Drive lacks provider-enforced immutability and its
OAuth token can read/delete authorized files, so Google account security, MFA, audit
review, and a separately stored recovery identity are essential compensating controls.

The selected layout deliberately does not claim strict 3-2-1: the local set shares the
Mac's internal disk while Google Drive is the second medium/offsite copy. Do not present
this as an unmet setup action or repeatedly ask the owner for an external drive; revisit
it only if the owner changes the recovery objective.

## Failure response

### Scheduled job failed or became a dead letter

1. Run the status command and record only its reason codes and job ID.
2. Check free space, mount availability, container health, and PostgreSQL health.
3. Do not paste raw `pg_dump`, provider, or filesystem errors into Beads.
4. Correct the operational cause, then enqueue a new idempotency key or run one manual
   backup. Do not rewrite the failed row to claim success.
5. Verify the new public envelope and confirm status returns healthy.

### Offsite credential compromise

1. Revoke the rclone OAuth grant in the Google account and stop the backup worker.
2. Reauthorize to a new mode-0600 external config; never reuse or paste its token.
3. Inspect Google account/Drive activity for unexpected reads, changes, or deletions.
4. Verify local ciphertext checksums against remote size/checksum metadata.
5. Create a fresh encrypted set and repeat the download/decrypt verification before
   restarting scheduling.

### Lost or failed local backup medium

1. Stop retention; keep the live database and offsite copy unchanged.
2. Replace with a newly encrypted, owner-only dedicated medium.
3. Run a manual local backup and verify its envelope.
4. Confirm the offsite copy independently before resuming retention.
5. Treat a directory on the live disk as temporary only, not as the recovered second
   medium.

### Lost private age identity

1. Test the password-manager and sealed offline recovery copies separately.
2. If neither works, existing sets are unrecoverable; do not report them healthy.
3. Generate a new identity off-host, update only the public recipient, create a new
   backup, and immediately complete a synthetic decrypt/restore check.
4. Keep any recovered old identity until all sets encrypted to it age out.

### Corrupt or incomplete set

1. Do not delete or rename it during initial investigation; status protects it from
   retention automatically.
2. Verify the public ciphertext checksum and compare the offsite object's metadata.
3. Select the newest earlier set whose checksum passes; never assume the newest file
   is usable.
4. Create a fresh set, then run an isolated restore drill. A checksum pass alone does
   not prove the private key or application restore path works.

### PostgreSQL major upgrade

1. Stop the scheduled backup worker and create a verified pre-upgrade set.
2. Complete an isolated restore using the currently pinned PostgreSQL major version.
3. Update the database image, backup client stage, compatibility checks, and ADR before
   upgrading production.
4. Run the full PostgreSQL integration suite, create a post-upgrade set, and repeat the
   restore drill. Do not resume retention until both sets are accounted for.

### Emergency host replacement

1. Provision an isolated replacement with no public ingress.
2. Install the repository revision and pinned images named by the selected set.
3. Retrieve ciphertext, verify its public envelope, and recover the private identity
   through the separate recovery process.
4. Follow `docs/backup-restore-design.md` section 8: decrypt privately, validate every
   component checksum, restore PostgreSQL, reapply privileges, restore artifacts, and
   reissue application secrets separately.
5. Run safety, authentication, timeline/export, and AI-role write-denial checks before
   any DNS/tunnel/cutover change. Production cutover requires explicit authorization.

## Restore drill cadence

Run `hc-cbs.2` at least quarterly and after changes to PostgreSQL major version,
encryption, artifact storage, backup format, or deployment topology. The drill must use
the newest offsite set, exercise separately held recovery material, meet the four-hour
RTO, prove the 24-hour RPO, and remove isolated plaintext afterward. A failed drill
blocks release and creates a remediation Beads issue.
