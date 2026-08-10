# Operations, monitoring, and incident runbook

This runbook is for the single-owner HealthCurve deployment. Operational alerts
contain reason codes and counts only. They never contain symptoms, doses, diary text,
lab values, prompts, filenames, email addresses, or credentials.

HealthCurve is not an emergency alerting or clinical decision system. A software
alert must never delay emergency care or replace the physician-approved plan.

## Monitoring topology

Two independent paths are required:

1. The **on-host monitor** evaluates database queue age/dead letters, rolling request,
   authentication and model failures, Garmin import age when enabled, backup age,
   Ollama availability, and disk free space. It sends transition alerts and six-hour
   reminders to the owner's Telegram account using the encrypted bot credential.
2. The **off-host probe** runs on a different physical host that is an explicitly
   authorized tailnet device and checks the private `/health/ready` URL. It sends
   outage and recovery notifications to a private ntfy-compatible HTTPS topic. It
   must not run on the HealthCurve host: an on-host probe cannot report host, power,
   ISP, Docker, or Tailscale failure.

The on-host monitor exits nonzero if Telegram delivery is not configured. Production
is not ready until both paths have delivered a controlled test notification to a
device the owner carries.

### Start the on-host monitor

The encrypted Telegram credential and owner chat ID must already be configured. Set
the backup directory to the separate local backup medium, then run:

```bash
docker compose -f docker-compose.yml -f deploy/monitoring.compose.yml \
  up -d --build monitor
docker compose -f docker-compose.yml -f deploy/monitoring.compose.yml \
  logs --tail=100 monitor
```

For a one-time privacy-safe JSON check:

```bash
docker compose -f docker-compose.yml -f deploy/monitoring.compose.yml \
  run --rm monitor python -m healthcurve.monitor --once
```

Exit `0` means healthy, `2` means one or more checks alert, and `1` means monitoring
or delivery could not be initialized. Configure `HC_MONITOR_GARMIN_AGE_LIMIT_H` only
when imports are expected on a schedule; leaving it unset marks that check disabled,
not healthy and not zero.

### Install the independent probe

Copy `deploy/offhost-monitor.py` to a separate maintained machine. Protect the private
ntfy topic URL as a credential and run the probe every minute using that machine's
scheduler:

```bash
HC_TARGET_URL=https://machine.example-tailnet.ts.net/health/ready \
HC_ALERT_URL=https://notify.example/private-random-topic \
HC_STATE_FILE=/var/lib/healthcurve-monitor/state \
python3 /opt/healthcurve/offhost-monitor.py
```

The probe host must be narrowly allowed to reach TCP 443 by the tailnet policy. The
state directory must be owner-writable and mode `0700`; the environment file must be
mode `0600`. A first run sends the current transition. Test failure by using a
nonexistent target temporarily, verify the off-host notification, restore the target,
and verify the recovery notification. Never put the private URL or alert URL in Git,
Beads, or shell history.

## Signals and first response

| Reason code | Meaning | First response |
|---|---|---|
| `monitoring_collection_failed` | Database or collector failed | Check container and PostgreSQL health; use the off-host probe as truth for reachability. |
| `operational_telemetry_unavailable` | Redis counters cannot be read | Restore Redis; rate-limited operations intentionally fail closed. |
| `request_errors_repeated` | At least the configured 5xx threshold in 5 minutes | Inspect redacted API logs by correlation/time; do not enable debug in production. |
| `auth_failures_repeated` | At least the configured failed-login threshold in 15 minutes | Revoke sessions, verify Tailscale devices, and begin the device-loss procedure if unexplained. |
| `model_unavailable` / `model_failures_repeated` | Ollama is down or repeatedly failing | Keep using deterministic Telegram commands; check the private Ollama listener and model availability. |
| `queue_stalled` / `queue_dead_letter` | Due work is old or permanently failed | Check worker health and safe reason codes; do not replay a job until its idempotency behavior is understood. |
| `lab_document_cleanup_failed` / `report_artifact_cleanup_failed` | An authorized privacy deletion committed, but tombstoned private-file cleanup is retrying or exhausted | Keep the database deletion intact; inspect the isolated `cleanup-worker`, storage mount availability, and permissions using opaque IDs only. Restore the mount and allow the idempotent job to retry. Never recreate the deleted database rows or manually remove broad directories. |
| `garmin_import_stopped` | No confirmed import inside the configured interval | Verify the expected schedule and source export; missing data stays missing, never zero. |
| `backup_missing` / `backup_age_warning` / `backup_integrity_failed` / `backup_job_failed` | Backup absent, stale, damaged, or failed | Follow `backup-runbook.md`; do not delete the last known-good set. |
| `disk_space_low` | Free space is at or below the configured percentage | Stop nonessential imports, locate growth using metadata only, and add capacity. Never delete health data or backups casually. |

## General incident procedure

1. Protect the person first. Use ordinary emergency care and the physician-approved
   plan; HealthCurve availability is secondary.
2. Record the alert time and reason codes outside HealthCurve. Do not copy health
   records or secrets into an incident note or support ticket.
3. If compromise is plausible, disconnect the public/tailnet edge while leaving disks
   and containers intact for evidence. Do not wipe, prune, reset, or rotate blindly.
4. Check the independent probe, host power/network, Docker service state, disk, then
   PostgreSQL/Redis/API/worker/monitor in that order.
5. Use structured logs around the alert time. Exception messages and database query
   output are not safe to paste into third-party systems.
6. Restore service using the smallest reversible action. Validate login, one read-only
   timeline request, worker queue age, a deterministic Telegram command, and backup
   status. Do not use real health values in a test message.
7. Confirm both on-host and off-host recovery alerts. Record cause, duration, actions,
   and any follow-up as Beads issues. Security findings block release.

## Restore and data-loss procedure

Use [backup-runbook.md](backup-runbook.md). Restore into an isolated network first,
verify manifest signatures/checksums and schema revision, run the complete restore
validation, then deliberately promote the recovered instance. Never test a restore by
overwriting the only live copy. The credential key ring is restored through its
separate recovery channel, never from the database backup itself.

## Credential-key loss

The external credential key ring encrypts integration tokens and is intentionally not
recoverable from PostgreSQL.

1. If one copy remains, take the app offline, make an encrypted verified copy, add a
   new active key, rotate all credentials, verify integrations, then retire the old
   key only after no rows reference it.
2. If every copy is lost, encrypted integration credentials are unrecoverable by
   design. Do not weaken encryption or edit ciphertext. Revoke the Telegram token and
   any provider tokens at their providers, create a new key ring, issue fresh tokens,
   and store them with `credential-set`.
3. Existing health facts and physician-approved plans remain readable; only encrypted
   integration credentials are affected. Verify this before reconnecting providers.
4. Record the event and remediation as a security issue without storing tokens or key
   material in Beads.

## Lost or stolen device

1. From a trusted device, remove the lost device from the tailnet immediately and
   expire its keys/sessions at the identity provider.
2. Use **log out everywhere** in HealthCurve. Change the account password and rotate
   the second factor/recovery codes once MFA is enabled.
3. Revoke browser credentials, Telegram sessions, and any provider tokens accessible
   from the device. Rotate the Telegram bot token if its operator credentials might
   have been exposed.
4. Check audit entries and authentication-failure alerts from before and after loss.
   Treat unexplained access as a security incident.
5. Use platform remote lock/erase only after evidence and current backups are secured.
   HealthCurve's server-side record is not erased by wiping a client device.

## Alert test schedule

Run quarterly and after monitoring changes:

- stop the API edge and verify off-host outage/recovery notifications;
- inject synthetic operational counters until request/auth/model thresholds alert;
- enqueue a synthetic overdue job and verify `queue_stalled`;
- point the test monitor at an empty synthetic backup directory;
- use an injected disk-usage fixture in automated tests (never fill a real disk);
- configure an old synthetic Garmin import and verify lag alerting;
- run `python -m healthcurve.monitor --once` and archive only its operational output.

Record date, receiver, delivery latency, and result. Do not record notification URLs,
bot tokens, device identifiers, or health content.
