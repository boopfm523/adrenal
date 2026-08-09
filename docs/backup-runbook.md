# HealthCurve Backup Operations Runbook

**Scope:** encrypted local backups, nightly scheduling, retention, integrity status,
and the boundary for an offsite copy. The isolated restore drill is tracked separately
by `hc-cbs.2` and remains a production-launch gate.

## Safety rules

- The backup host stores only an `age` **public recipient**. Keep the private identity
  off the HealthCurve host, out of `.env`, Git, logs, Beads, screenshots, and backups.
- Use a dedicated local/external backup medium. A second directory on the live database
  disk does not meet the three-copy design.
- The routine offsite credential may inspect metadata and create objects only. It must
  not read bodies, replace objects, change retention, or delete. Keep the separately
  controlled maintenance/deletion credential out of the backup worker environment.
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

Create three directories: the dedicated local backup destination, uploads source, and
report-artifact source. On Linux the bind-mounted directories must be accessible to
UID/GID `10001:10001`; keep the destination owner-only. On macOS Docker Desktop handles
the UID mapping, but the host directory should still be private to the owner.

`HC_UPLOADS_DIR` is also mounted read/write into the API and the no-network document
worker. It contains retained source medical PDFs plus opaque validation state, so it
must not be placed under the web root or inside Git. The backup worker mounts that same
host directory read-only and includes it in every encrypted backup set.

Do not use the repository, PostgreSQL data volume, `/tmp`, or an unencrypted shared
folder as the production destination.

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

A concrete provider adapter and real provider account are intentionally not selected
in the repository. That decision requires the owner's provider/account, terms, cost,
region, object-lock, and credential choices. Until that Beads dependency is completed,
leave:

```dotenv
HC_BACKUP_OFFSITE_ENABLED=false
```

Do not treat local-only status as satisfying the production three-copy requirement.
When a reviewed adapter is installed, activation additionally requires:

```dotenv
HC_BACKUP_OFFSITE_ENABLED=true
HC_BACKUP_OFFSITE_PROVIDER=<registered adapter name>
HC_BACKUP_OFFSITE_DESTINATION=<private bucket/prefix>
HC_BACKUP_OFFSITE_CREDENTIAL_FILE=<absolute mode-0600 mounted routine credential>
```

The worker refuses incomplete configuration, empty/linked/group-readable credential
files, a maintenance credential in its environment, a missing adapter, upload conflict,
or remote metadata mismatch. Never put credential contents in `.env`; mount the file
read-only through a provider-specific Compose override.

## Failure response

### Scheduled job failed or became a dead letter

1. Run the status command and record only its reason codes and job ID.
2. Check free space, mount availability, container health, and PostgreSQL health.
3. Do not paste raw `pg_dump`, provider, or filesystem errors into Beads.
4. Correct the operational cause, then enqueue a new idempotency key or run one manual
   backup. Do not rewrite the failed row to claim success.
5. Verify the new public envelope and confirm status returns healthy.

### Offsite credential compromise

1. Disable the routine credential at the provider; do not use a delete-capable key.
2. Rotate to a new create/head-only credential stored as a mode-0600 mounted file.
3. Inspect provider audit metadata for unexpected creates or reads without downloading
   objects to an untrusted host.
4. Verify local ciphertext checksums against remote size/checksum metadata.
5. Use the separately controlled maintenance identity only if an explicit, reviewed
   cleanup is required.

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
