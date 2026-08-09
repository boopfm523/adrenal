# MFA enrollment and recovery

HealthCurve uses password plus a time-based one-time password (TOTP). The authenticator
seed is encrypted in PostgreSQL with the external credential key ring; the database
alone cannot decrypt it. Ten high-entropy recovery codes are generated at enrollment,
stored only as SHA-256 digests, shown once, and consumed once.

## Enroll before production

Create and mount the external credential key ring first. While the deployment is
still private, enroll from the trusted local host:

```bash
docker compose run --rm api python -m healthcurve.cli mfa-enroll
```

Enter the current password at the hidden prompt, add the printed secret/URI to a
standard authenticator, and enter its current six-digit code. Store the recovery codes
off-device in a protected password manager or sealed physical copy. Do not screenshot
them, paste them into chat, put them in Beads, or save them in the repository.

Then set `HC_MFA_REQUIRED=true` and restart. Production refuses to start with that
setting false. If the database owner is not enrolled, production login fails closed
and directs the operator to the local enrollment command.

An already signed-in owner can also enroll under **Settings → Multi-factor
authentication**. The seed is shown only between starting and confirming enrollment.
Enrollment and removal are audited. TOTP steps cannot be replayed, and recovery-code
use is audited without storing the code.

## Normal recovery

At the login form, enter a saved recovery code in the same field as the authenticator
code. It works once. After signing in:

1. Open Settings → Multi-factor authentication.
2. Generate replacement recovery codes using the password and either the working
   authenticator or another unused recovery code.
3. Save the replacements immediately. Every old code is invalidated.
4. If the authenticator device was lost, add the account to a replacement device from
   the protected authenticator backup before removing MFA.

Removing MFA requires the current password and a valid current second factor, revokes
every session, destroys the encrypted seed and all recovery-code hashes, and is
audited. With `HC_MFA_REQUIRED=true`, password-only login remains blocked until local
enrollment is completed again.

## If every factor and recovery code is lost

There is no password-only administrator bypass, emailed reset link, support override,
database flag flip, or endpoint that weakens the second-factor requirement. Use one of
the pre-established recovery materials:

- the authenticator application's separately protected backup/recovery;
- an unused HealthCurve recovery code stored off-device; or
- a verified isolated restore that includes the encrypted seed **and** the separately
  backed-up credential key ring, together with the authenticator seed backup.

If none exists, access cannot be recovered without deliberately removing the security
control outside supported HealthCurve workflows. Treat that as loss of access, not as
an excuse to add a bypass. Preserve the encrypted record and seek a reviewed recovery
change tracked as a security issue; do not edit production identity rows manually.

## Loss and compromise response

For a lost device, remove it from Tailscale, log out every HealthCurve session, consume
or replace recovery codes, and rotate the authenticator enrollment if exposure is
plausible. For a stolen credential key ring, take the service private, rotate the key
ring and all encrypted credentials, then re-enroll MFA. Follow
[operations-runbook.md](operations-runbook.md) and never put secrets in incident logs.

Quarterly, verify that one protected recovery code is readable without consuming it,
that authenticator time is synchronized, and that the separate credential-key backup
is restorable. Use synthetic accounts for destructive recovery drills.
