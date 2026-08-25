# Public static HealthCurve

The public site at `https://jeffellin.com/healthcurve/` is a read-only static
projection of the private HealthCurve application. It contains HTML, CSS,
JavaScript, and reviewed JSON only. It has no database connection, private API
dependency, authentication form, or data-entry controls.

The owner has explicitly authorized publication of the real curve data in this
projection. The generated data and built bundle remain ignored by Git. The
privacy boundary, exact allow-list, exclusions, and eligibility rule are defined
in [ADR 0029](adr/0029-public-static-healthcurve.md).

## Publication eligibility

A local calendar day is exported only when both conditions are true:

1. local noon on the following day has passed (the day ended plus 12 hours); and
2. a successful or successful-with-warnings Garmin sync covers that day and
   finished after the cutoff.

The exporter fails closed: an ineligible day is absent rather than partially
published. The calendar is derived from the manifest and defaults to its newest
published date.

The default Garmin schedule runs at local noon and midnight. The noon run supplies
the required post-cutoff provenance for the preceding day, so the next two-hour
publisher run can normally publish it shortly after the twelve-hour cutoff. A delayed
or failed Garmin run still keeps that day unpublished until a later successful run.

## One-time SSH setup

The deployment account is `jellin2`, the hostname is `jeffellin.com`, and the
port is `22`. Keep those values separate: setting the hostname to
`jellin2@jeffellin.com` and then adding the account again produces the invalid
destination `jellin2@jellin2@jeffellin.com`.

On the server, add this exact restricted public-key entry to
`/home/jellin2/.ssh/authorized_keys`:

```text
restrict,no-user-rc,command="/usr/bin/rrsync -wo /home/jellin2/jeffellin.com/healthcurve" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJ9Gald1YT/uy1oFJZWTU+ey/7dhDZTdZ4n/kkNf9UGj HealthCurve static-site deployment
```

The forced `rrsync` command limits the key to writes under the exact public
webroot. It cannot provide an interactive shell or read files back. The expected
server ED25519 fingerprint is:

```text
SHA256:IuO3r8KWrx3xcCM3nVBWk2eNvbFIKI0exWIJrrtd76Y
```

The client uses the dedicated private key at
`/Users/jeff/.ssh/healthcurve_public_deploy`. Never copy that key into Git or to
the webroot. Publishing requires modern rsync; this Mac uses
`/opt/homebrew/bin/rsync`. The older macOS `/usr/bin/rsync` sends a command that
the server's restricted wrapper rejects.

## Manual verification and publication

From the repository root, exercise the complete build, export, privacy
verification, and restricted transfer without changing the server:

```bash
HC_PUBLIC_HEALTHCURVE_DRY_RUN=true scripts/publish_public_healthcurve.sh
```

Publish the verified bundle:

```bash
scripts/publish_public_healthcurve.sh
```

The script pins the known host fingerprint, uses batch-only key authentication,
atomically replaces the local bundle, and transfers only after verification.
Its success/failure messages contain no health values. Because the server key is
write-only, obsolete hashed assets are not remotely deleted; the current
manifest is authoritative and browsers cannot discover obsolete files through
the UI. Remove obsolete files only through a normal server login when needed.

Verify production without exposing payload contents in logs:

```bash
curl --fail --silent --show-error --output /dev/null \
  https://jeffellin.com/healthcurve/
curl --fail --silent --show-error --output /dev/null \
  https://jeffellin.com/healthcurve/data/manifest.json
```

## Automatic publication

The sample LaunchAgent runs every two hours and at login. It is safe to run more
often than the eligibility window because the exporter independently enforces
the cutoff and Garmin-completion rule. macOS blocks background agents from the
interactive repository under `Documents`, so the agent uses a private runtime
clone under `Library/Application Support`. The scheduled wrapper fast-forwards
that clone from GitHub and refreshes frontend dependencies only when the lockfile
changes.

```bash
mkdir -p "/Users/jeff/Library/Application Support/HealthCurvePublisher"
git clone https://github.com/boopfm523/adrenal.git \
  "/Users/jeff/Library/Application Support/HealthCurvePublisher/repo"
install -m 600 .env \
  "/Users/jeff/Library/Application Support/HealthCurvePublisher/repo/.env"
npm --prefix "/Users/jeff/Library/Application Support/HealthCurvePublisher/repo/frontend" ci
install -m 600 deploy/com.healthcurve.public-healthcurve.plist.example \
  /Users/jeff/Library/LaunchAgents/com.healthcurve.public-healthcurve.plist
launchctl bootstrap gui/$(id -u) \
  /Users/jeff/Library/LaunchAgents/com.healthcurve.public-healthcurve.plist
launchctl kickstart -k gui/$(id -u)/com.healthcurve.public-healthcurve
```

Inspect status and privacy-safe logs:

```bash
launchctl print gui/$(id -u)/com.healthcurve.public-healthcurve
tail -n 100 "/Users/jeff/Library/Application Support/HealthCurvePublisher/publisher.log"
tail -n 100 "/Users/jeff/Library/Application Support/HealthCurvePublisher/publisher-error.log"
```

If Docker Desktop, the database, network, or remote host is unavailable, the run
fails without transferring a partial bundle and the next scheduled run retries.
To revoke publication access, remove the dedicated public-key line from
`authorized_keys` using a normal server login. To unload the scheduler:

```bash
launchctl bootout gui/$(id -u)/com.healthcurve.public-healthcurve
```
