# Integration credential encryption and rotation

Garmin OAuth tokens, the Telegram bot token and webhook secret, and future weather API
keys are class C8. HealthCurve encrypts them with AES-256-GCM before PostgreSQL sees
them. The database stores a key ID, random nonce, and authenticated ciphertext. The
key ring stays in an owner-only file outside the database and repository, so a database
dump alone cannot recover a credential.

Provider secrets are never included in exports, logs, audit details, reports, or model
prompts. Production refuses plaintext Telegram secrets in environment variables.

## Create and mount the key ring

Choose a path outside the repository and create it once:

```bash
mkdir -p "$HOME/.config/healthcurve"
uv run python -m healthcurve.cli credential-key-init \
  "$HOME/.config/healthcurve/credential-keys.json" --key-id key_2026_08
chmod 600 "$HOME/.config/healthcurve/credential-keys.json"
```

The command will not overwrite an existing file. Back this file up in the owner's
password manager or encrypted vault, separately from database backups. Losing it makes
the provider credentials unrecoverable; a database backup does not contain the key.

For Compose, set only its host path in `.env`:

```dotenv
HC_CREDENTIAL_KEY_FILE_HOST=/Users/you/.config/healthcurve/credential-keys.json
```

Then include the opt-in mount on commands that run the API or worker:

```bash
docker compose -f docker-compose.yml -f deploy/credentials.compose.yml up -d
```

For a non-Compose process, set `HC_CREDENTIAL_KEY_FILE` directly to the file path.
The application refuses a symlink, non-regular file, or group/world-accessible key
ring.

## Store or destroy a credential

The preferred input is a hidden prompt, which avoids shell history and process lists:

```bash
docker compose -f docker-compose.yml -f deploy/credentials.compose.yml run --rm api \
  python -m healthcurve.cli credential-set telegram bot_token
```

Known Telegram names are `bot_token` and, for the non-default webhook transport,
`webhook_secret`. Disconnecting an integration must destroy its ciphertext:

```bash
docker compose -f docker-compose.yml -f deploy/credentials.compose.yml run --rm api \
  python -m healthcurve.cli credential-delete telegram bot_token
```

The audit trail records only provider/name metadata and the opaque row ID, never the
credential value or ciphertext.

## Rotate without data loss

Rotation retains the old key until all rows have been authenticated and re-encrypted:

```bash
uv run python -m healthcurve.cli credential-key-add \
  "$HOME/.config/healthcurve/credential-keys.json" --key-id key_2026_11

docker compose -f docker-compose.yml -f deploy/credentials.compose.yml run --rm api \
  python -m healthcurve.cli credential-rotate

HC_KEY_PATH="$HOME/.config/healthcurve/credential-keys.json"
docker compose run --rm \
  -v "$HC_KEY_PATH:/tmp/credential-keys.json:rw" \
  -e HC_CREDENTIAL_KEY_FILE=/tmp/credential-keys.json api \
  python -m healthcurve.cli credential-key-retire \
  /tmp/credential-keys.json --key-id key_2026_08
```

`credential-rotate` runs in one database transaction: a bad or missing old key rolls
back the entire operation. `credential-key-retire` queries PostgreSQL and refuses to
remove a key while any row still names it. After rotation, restart API and worker
processes and replace the separately held key-ring backup.

If the database is restored from a point before rotation, restore the matching key-ring
version too. Do not retire a key from every backup until all retained database backups
that reference it have expired, or archive the old key ring with those encrypted
backups.

## Compromise response

If only a database dump leaks, rotate provider credentials as a precaution; the dump
does not contain the AES keys. If the key ring and database may both have leaked,
revoke/rotate every provider credential at its provider first, then create a new key,
store the replacement credentials, and rotate the remaining rows. Never paste a token,
key-ring content, or provider response into logs or a Beads issue.
