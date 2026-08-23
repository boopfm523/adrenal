# Private-use release checklist

Run this procedure before treating HealthCurve as the dependable home for real data,
and repeat the affected checks after deployment, database, backup, or network changes.
It is deliberately scoped to one owner on localhost and authenticated Tailscale. It
does not turn HealthCurve into a public service. The owner-approved runtime may remain
`HC_ENVIRONMENT=dev`; debug must still be off and secrets must not be examples or test
credentials.

Run commands from `$HOME/Documents/adrenal`. Stop on the first failure. Commands
below either print non-sensitive status or explicitly suppress secret-bearing output.
Never paste `.env`, an unredacted Compose configuration, OAuth material, the backup
recovery identity, or medical content into a release record.

## 1. Tested revision

Run the complete repository gate on the exact revision intended for use:

```bash
git status --short --branch
make check
```

Pass when the expected branch/revision is present, no unexplained changes are included,
and every check succeeds. Existing intentional local-only files may remain uncommitted;
identify them locally without copying their content into Beads.

## 2. Private secrets and debug-off

```bash
uv run python scripts/check_private_env.py .env
git check-ignore -q .env
```

Pass when the checker reports owner-only permissions, debug off, and distinct,
non-placeholder passwords for the normal and restricted-AI database roles, and
`git check-ignore` exits zero. The checker never prints values. A private development
runtime is acceptable; `HC_DEBUG=true`, shared database-role credentials, sample
passwords, a symlinked `.env`, or group/other file access is not.

## 3. Alembic head

```bash
docker compose exec -T api alembic current
```

Pass only when the single current revision is marked `(head)`. If it is behind, run
the documented `make migrate` procedure and recheck; do not improvise a rollback on the
only database.

## 4. No public listeners

Validate the rendered Compose topology without displaying its environment values:

```bash
docker compose config --format json \
  | uv run python scripts/check_compose_topology.py /dev/stdin
```

Pass when it reports `topology ok: loopback development edge`. Then run the host and
tailnet checks from `docs/tailscale-hosting.md`:

```bash
lsof -nP -iTCP:8080 -sTCP:LISTEN
lsof -nP -iTCP:11434 -sTCP:LISTEN
lsof -nP -iTCP:5432 -sTCP:LISTEN
lsof -nP -iTCP:6379 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
/Applications/Tailscale.app/Contents/MacOS/Tailscale serve status
curl -fsS http://127.0.0.1:8080/health/ready
```

Pass when 8080 and host-native Ollama are loopback-only, PostgreSQL/Redis/API have no
host listeners, Tailscale Serve says `tailnet only` and proxies to loopback, and the
readiness response is exactly `{"status":"ok"}`. Also repeat the behavioral boundary:
an approved phone works while connected to Tailscale and cannot connect after
Tailscale is disconnected. Funnel, router forwarding, a LAN listener, or a public edge
fails this checklist.

## 5. Encrypted backup health

Use the same overlays as the scheduled backup service:

```bash
docker compose \
  -f docker-compose.yml \
  -f deploy/backup.compose.yml \
  -f deploy/google-drive-backup.compose.yml \
  exec -T backup-worker python -m healthcurve.backup_status

uv run python scripts/check_rclone_drive_config.py \
  --config "$HOME/.config/healthcurve/rclone.conf"
```

Pass when backup status is `healthy`, no reason codes or dead letter are present, the
newest encrypted set meets the documented 24-hour RPO, and the private Drive verifier
passes. Use only the active Drive folder named `HealthCurve Backups`. A successful
upload alone does not prove recovery.

## 6. Isolated restore drill

```bash
uv run python scripts/run_restore_drill.py \
  --config "$HOME/.config/healthcurve/rclone.conf" \
  --prompt-identity
```

At the hidden prompt, paste only the `AGE-SECRET-KEY-...` identity from the macOS
Passwords entry. Pass only when output says `"status": "verified"`, every verification
boolean is true, both `rpo_met` and `rto_met` are true, and teardown is verified. Never
put the identity in the command, environment, Git, Beads, a screenshot, or chat. The
completed 2026-08-10 drill is the baseline; rerun quarterly and after backup-format,
encryption, database-major, or topology changes.

## 7. Security review

Run the repository-owned current-tree and full-history scans. They report rule and
location metadata with candidate values redacted. An unreviewed history finding blocks
publication; false positives require an explicit hash-bound entry with a reason in
`.secrets.history-reviews.json`. Candidates already reviewed as false positives in
`.secrets.baseline` reuse that decision. Never paste a candidate value into either file.

```bash
make secrets
```

Pass only when both scans report no unresolved or confirmed secrets. This scans every
reachable Git blob, including values removed from the current working tree. Scanner
metadata, Beads state, and package-manager lockfiles use the same explicit exclusions as
the current-tree scanner because their stored hashes are not credentials. A confirmed
credential must be removed from the proposed public history and rotated before release.

```bash
bd show hc-cbs.1
```

Pass when the Tailscale-only security review is closed and has no unresolved P0/P1
follow-up that applies to the current private topology. A future public edge, shared
users, or non-tailnet access invalidates that review and requires a new one.

## 8. Emergency page with optional services offline

Run the PostgreSQL-backed safety test. Its application fixture has no Redis, worker,
Telegram, Garmin, weather, or Ollama dependency and disables JavaScript reliance:

```bash
uv run pytest -m postgres tests/integration/test_api_safety.py \
  -k test_emergency_page_renders_without_ai_or_javascript
```

Pass when the test succeeds. Then load `/emergency` once from the local site and verify
that it displays recorded facts or physician-authored instructions only, contains the
emergency-services warning, and does not depend on generated analysis. This check does
not authorize inventing or changing physician instructions.

## 9. Record the result

Record the date, Git revision, pass/fail gate names, and privacy-safe reason codes in
the applicable Beads issue. Do not record secret values, account identifiers, tailnet
URLs, medical values, filenames containing personal information, or recovery material.
Any failed security, backup, restore, migration, or emergency-independence gate blocks
reliance on the app until a linked Beads issue is fixed and the gate is rerun.
