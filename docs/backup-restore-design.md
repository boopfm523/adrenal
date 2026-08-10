# HealthCurve Backup and Restore Design

**Status:** Phase 0 design; implementation is tracked by `hc-cbs.8` and the first
isolated restore drill by `hc-cbs.2`.

## 1. Objectives and boundaries

HealthCurve's backup system protects the complete longitudinal record from host loss,
operator error, corrupted storage, and failed upgrades without creating an easier way
to steal the record.

Initial service objectives:

- **Recovery point objective (RPO): 24 hours.** A successful encrypted backup is
  produced and copied offsite every night. An alert fires before backup age reaches
  26 hours, allowing intervention close to the 24-hour target.
- **Recovery time objective (RTO): 4 hours.** From declaring the primary host unusable,
  an operator with the recovery material can provision the documented isolated stack,
  restore the most recent valid backup, complete acceptance checks, and make the
  service ready for the owner within four hours.

The initial backup design covers one owner and one PostgreSQL/Compose deployment. It
does not provide continuous replication, point-in-time recovery, multi-region failover,
or zero-downtime restore. Those are disproportionate to the stated objectives.

## 2. Backup contents

Each backup set has one immutable set ID containing:

1. A PostgreSQL custom-format logical dump created with `pg_dump --format=custom`.
   It includes all HealthCurve schemas, migration state, facts, plans, AI records,
   identity data, audit records, import/job state, and report snapshot manifests.
2. User-uploaded source documents and retained report artifacts from their configured
   persistent volumes or storage root.
3. Restore configuration that is safe to copy: pinned Compose/deployment manifests,
   Caddy configuration, Alembic configuration, application version/commit, PostgreSQL
   major version, Alembic head, and an inventory of required secret names. Secret
   values and `.env` files are excluded.
4. A manifest containing set ID, UTC start/end, source environment, component versions,
   file sizes, SHA-256 checksums, database dump inventory result, encryption recipient
   fingerprint, and application schema version.

The following are deliberately excluded:

- Redis, because it is not a durability domain.
- Ollama model files, because they are large and reproducibly pulled by exact model
  name/digest; the required model inventory belongs in the manifest.
- Build caches, logs, temporary OCR files, extraction scratch data, and container images.
- Integration tokens or other secrets copied from environment files. Database-resident
  encrypted token ciphertext is naturally present in the database dump, but the
  application encryption key is recovered through the separate secret-recovery process.

## 3. Backup pipeline

The production implementation should run from a dedicated least-privilege backup
runner, not from an HTTP request and not with the application's normal credentials.
Scheduling may be represented in `ops.job` per ADR-0004, while host-level execution
and offsite credentials remain isolated from the API and general worker.

Nightly sequence:

1. Acquire a singleton backup lock and create a private working directory (`0700`) on
   encrypted local storage with enough capacity for at least two uncompressed sets.
2. Record versions and Alembic head. Refuse a backup when the PostgreSQL major version
   is unsupported by the pinned restore image.
3. Run `pg_dump --format=custom` using a dedicated read-only backup role with the
   minimum privileges required to dump all HealthCurve schemas.
4. Run `pg_restore --list` against the dump and fail if it cannot enumerate the archive.
5. Copy the artifact/upload scope using a snapshot-consistent mechanism. Report
   artifacts are content-addressed or immutable while a backup reads them; mutable
   uploads require a brief application-coordinated snapshot boundary.
6. Build the manifest and verify every plaintext component checksum.
7. Package the set and encrypt it locally with `age` to a pinned X25519 recipient.
   Only the recipient **public key** is present on the production host.
8. Compute the encrypted archive checksum. Delete plaintext working files only after
   encryption success, using exact validated paths inside the private working directory.
9. Write the encrypted archive and manifest/checksum envelope to the local backup
   medium using an atomic temporary name followed by rename.
10. Upload the encrypted objects to separately credentialed offsite storage over TLS.
    The initial owner-approved Google Drive provider cannot enforce a write-only role or
    object lock; the application adapter nevertheless has no delete/replace operation
    and uses immutable creates. This accepted downgrade and its compensating controls
    are documented in the runbook and `hc-cbs.8.4`.
11. Confirm the remote object exists with the expected size and checksum metadata.
12. Record only operational metadata in HealthCurve: set ID, timestamps, size,
    encrypted checksum, locations, outcome, and redacted failure category.

No plaintext dump is written to logs, issue tracking, or a general temporary directory.
Partial encrypted objects use a non-final name and are not retention candidates.

## 4. Encryption and key recovery

### Backup encryption

- Each set is encrypted before leaving the production host using `age` with a pinned
  recipient fingerprint stored in deployment configuration.
- The production host holds the public recipient only. Host compromise therefore does
  not reveal prior backup contents through the backup configuration.
- Encrypted archives are still stored on encrypted local media and in private offsite
  storage. These are defense-in-depth controls, not substitutes for archive encryption.
- The archive checksum covers ciphertext and is safe to retain with the object. The
  signed/controlled set manifest maps that ciphertext to the validated plaintext
  inventory without containing health values.

### Recovery material

Recovery requires two separately managed categories:

1. **Backup decryption identity:** the `age` private identity.
2. **Application secrets:** database/application secret inventory, field-encryption key,
   and any credentials that must be reissued rather than restored.

The decryption identity and application encryption key are never stored in the backup,
production repository, Beads, or the same account as the offsite archive. Maintain:

- One encrypted copy in the owner's password manager with recovery access configured.
- One sealed offline copy on encrypted removable media stored separately from the
  production host and local backup disk.
- A printed recovery inventory containing key fingerprints and locations, but not raw
  secret values, stored with the owner's emergency technical records.

The two copies protect against password-manager or physical-media loss. Anyone rotating
a key must retain the old identity until every set encrypted to it has aged out, update
the fingerprint in configuration, produce a test set, and complete a test decryption.
Key recovery is exercised during every quarterly restore drill. A backup whose identity
cannot be recovered is considered failed, regardless of upload status.

## 5. Three-copy layout

The originally proposed strict 3-2-1 layout was:

1. **Primary:** live PostgreSQL and artifact volumes on the HealthCurve host.
2. **Local backup:** age-encrypted sets on a dedicated local/external backup medium,
   not the live database volume.
3. **Offsite backup:** the same independently verified encrypted sets in versioned
   object storage under a separate account and credential.

The owner declined an external drive. The implemented personal-deployment layout keeps
the encrypted local set on the Mac's internal disk and a second encrypted set in Google
Drive. It has an offsite copy and two media, but only two independent failure domains:
Mac disk loss removes both the live database and local set. This is an explicit recovery
objective choice, not a pending request for external storage, and must not be described
as strict 3-2-1.

The preferred offsite service provides versioning and an immutability/object-lock
window. Google Drive does not: its OAuth grant can read/delete files created through
that authorization. HealthCurve narrows the application surface (no delete method,
immutable copy calls, exact checksum sidecars, dedicated-worker-only credential), but
account/token compromise can still erase remote history. The owner accepted this
residual risk for the initial personal deployment; Google account MFA/audit review and
separately stored recovery material are the chosen compensating controls.

## 6. Retention and deletion

Retain successful sets as:

- **7 daily:** the newest successful set for each of the last seven UTC days.
- **5 weekly:** the newest successful set in each of the last five ISO weeks.
- **12 monthly:** the newest successful set in each of the last twelve calendar months.

A single set may satisfy multiple buckets and is stored once. Failed, incomplete, or
unverified sets satisfy no bucket. Cleanup never deletes the last known good set.

This schedule provides dense recovery for recent operator errors, five weekly points
for delayed discovery, and one year of longitudinal recovery without indefinite copies
of deleted health data. The privacy page must disclose that deletion from the live
record does not remove existing encrypted sets immediately; deleted data ages out as
the final containing monthly set expires, no later than the documented retention
window unless a legal/incident hold is explicitly created.

Retention runs with the separate deletion-capable maintenance credential after a dry
run lists candidate set IDs. Local and offsite retention outcomes are reconciled. A set
is never removed solely because its counterpart is temporarily unreachable.

## 7. Automated integrity checks

Every nightly run must automatically verify:

- `pg_dump` exits successfully and `pg_restore --list` can enumerate the dump.
- Required schemas (`identity`, `fact`, `plan`, `ai`, and `ops`) and migration metadata
  appear in the inventory.
- Artifact/upload enumeration finishes without missing-file errors.
- Plaintext component checksums match before packaging.
- Encryption finishes and produces a non-empty final archive.
- Local ciphertext checksum matches the final file.
- Offsite object size and recorded checksum metadata match the local ciphertext.
- At least one valid local and one valid offsite set fall within the RPO window.
- The encryption recipient fingerprint matches the currently documented recovery key.

These checks prove archive structure, encryption completion, and copy consistency. They
do not prove recoverability of the private identity or application behavior; only the
isolated restore drill does that.

Alert on failed run, failed check, missing copy, unexpected size collapse/growth,
retention failure, or age approaching 26 hours. Alerts contain set IDs and error classes,
not database output, filenames derived from medical content, or health values.

## 8. Restore procedure

The restore runbook implemented by `hc-cbs.8` must automate and document:

1. Declare the restore reason, target set ID, source location, and start time.
2. Provision an isolated host or Compose project with no public ingress and outbound
   networking disabled except for retrieving the encrypted set when necessary.
3. Retrieve the encrypted archive and verify its ciphertext checksum.
4. Recover the correct private identity through the separately protected process;
   confirm its fingerprint before decrypting.
5. Decrypt into a private directory on encrypted storage and verify the manifest and
   every component checksum.
6. Start the pinned PostgreSQL major version and create empty roles/database without
   importing production secrets into shell history or logs.
7. Restore the custom-format dump with controlled ownership/ACL handling. Restore or
   reapply least-privilege grants and verify the restricted AI role cannot write to
   `fact` or `plan`.
8. Restore uploads and report artifacts to their configured paths and validate their
   checksums.
9. Install the application version named by the manifest, apply only documented
   forward migrations if required, and restore/reissue application secrets separately.
10. Run the acceptance checks below before any DNS, tunnel, or production traffic
    change.
11. Record elapsed time and findings in the restore-drill Beads issue without health
    data. Securely destroy the isolated plaintext copy after the drill or incident
    retention decision.

Do not restore over the only remaining live copy. A production cutover is a separate,
explicitly authorized step after acceptance.

## 9. Quarterly isolated restore drill

Run at least quarterly and after a material change to PostgreSQL major version,
encryption, artifact storage, backup format, or deployment topology. Use the newest
offsite set so the drill exercises offsite access and key recovery, not merely the local
copy.

The drill passes only when all of the following are evidenced:

- The selected set was created no more than 24 hours after the last successful set
  expected by its schedule (RPO evidence).
- The recovery identity is obtained through the documented separate process and its
  fingerprint matches.
- Ciphertext, manifest, database dump, uploads, and report artifacts pass checksums.
- PostgreSQL restores without unhandled errors and Alembic head is known.
- Required schemas, extensions, constraints, owner account, audit history, and job
  records exist.
- Automated safety tests confirm facts/plans/AI separation and confirm the AI role
  cannot write facts or plans.
- Synthetic sentinel records placed before the backup are queryable with exact decimal,
  source, correction, and timezone values. Real health values are not copied into drill
  notes or screenshots.
- A representative retained document/report opens and matches its checksum.
- API liveness/readiness and an authenticated read-only timeline/export smoke test pass
  with external integrations and Ollama disabled.
- The service reaches a documented “ready for owner cutover” state within four hours
  of drill start (RTO evidence).
- The isolated environment is securely torn down and plaintext working files removed.

A failed criterion fails the drill and creates a blocking Beads issue for production
release. The next drill occurs after remediation rather than waiting for the next
quarter.

## 10. Operational ownership and status

The Settings & privacy page may display last successful local/offsite set, age, next
scheduled run, last integrity result, and last restore drill. It must not expose object
credentials, bucket paths containing owner identity, encryption material, or filenames
derived from health content.

Implementation must provide a concise operator runbook for backup failure, offsite
credential compromise, lost local medium, key loss, corrupted set, PostgreSQL major
upgrade, and emergency host replacement. Production launch remains blocked until a
real encrypted set completes an isolated passing restore drill.
