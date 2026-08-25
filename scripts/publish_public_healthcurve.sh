#!/usr/bin/env bash
set -euo pipefail

PUBLIC_SITE_REPOSITORY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PUBLIC_SITE_FRONTEND="$PUBLIC_SITE_REPOSITORY/frontend"
PUBLIC_SITE_GENERATED="$PUBLIC_SITE_REPOSITORY/var/public-healthcurve"
PUBLIC_SITE_DATA="$PUBLIC_SITE_GENERATED/data"
PUBLIC_SITE_BUNDLE="$PUBLIC_SITE_GENERATED/bundle"
mkdir -p "$PUBLIC_SITE_GENERATED"
PUBLIC_SITE_STAGE="$(mktemp -d "$PUBLIC_SITE_GENERATED/.bundle-stage.XXXXXX")"
PUBLIC_SITE_RSYNC="${HC_PUBLIC_HEALTHCURVE_RSYNC:-/opt/homebrew/bin/rsync}"
PUBLIC_SITE_HOST="${HC_PUBLIC_HEALTHCURVE_HOST:-jeffellin.com}"
PUBLIC_SITE_ACCOUNT="${HC_PUBLIC_HEALTHCURVE_ACCOUNT:-jellin2}"
PUBLIC_SITE_PORT="${HC_PUBLIC_HEALTHCURVE_PORT:-22}"
PUBLIC_SITE_IDENTITY="${HC_PUBLIC_HEALTHCURVE_IDENTITY:-/Users/jeff/.ssh/healthcurve_public_deploy}"
PUBLIC_SITE_KNOWN_HOSTS="${HC_PUBLIC_HEALTHCURVE_KNOWN_HOSTS:-/Users/jeff/.ssh/known_hosts}"
PUBLIC_SITE_EXPECTED_HOST_KEY="${HC_PUBLIC_HEALTHCURVE_HOST_KEY_SHA256:-SHA256:IuO3r8KWrx3xcCM3nVBWk2eNvbFIKI0exWIJrrtd76Y}"
PUBLIC_SITE_DRY_RUN="${HC_PUBLIC_HEALTHCURVE_DRY_RUN:-false}"

cleanup() {
  if [[ -d "$PUBLIC_SITE_STAGE" ]]; then
    rm -r "$PUBLIC_SITE_STAGE"
  fi
}
trap cleanup EXIT

if [[ ! -x "$PUBLIC_SITE_RSYNC" ]]; then
  echo "public_publish_failed reason=current_rsync_missing" >&2
  exit 1
fi
if [[ ! -f "$PUBLIC_SITE_IDENTITY" || ! -f "$PUBLIC_SITE_KNOWN_HOSTS" ]]; then
  echo "public_publish_failed reason=ssh_material_missing" >&2
  exit 1
fi

PUBLIC_SITE_OBSERVED_HOST_KEY="$({
  ssh-keygen -F "$PUBLIC_SITE_HOST" -f "$PUBLIC_SITE_KNOWN_HOSTS" 2>/dev/null \
    | ssh-keygen -lf - -E sha256 2>/dev/null \
    | awk '$1 == "256" && $4 == "(ED25519)" {print $2; exit}'
} || true)"
if [[ "$PUBLIC_SITE_OBSERVED_HOST_KEY" != "$PUBLIC_SITE_EXPECTED_HOST_KEY" ]]; then
  echo "public_publish_failed reason=ssh_host_key_mismatch" >&2
  exit 1
fi

npm --prefix "$PUBLIC_SITE_FRONTEND" run build:public-healthcurve

docker compose \
  --project-directory "$PUBLIC_SITE_REPOSITORY" \
  -f "$PUBLIC_SITE_REPOSITORY/docker-compose.yml" \
  run --rm --no-deps \
  -v "$PUBLIC_SITE_REPOSITORY/src:/app/src:ro" \
  -v "$PUBLIC_SITE_GENERATED:/output" \
  api python -m healthcurve.public_site --output /output/data

"$PUBLIC_SITE_RSYNC" -a "$PUBLIC_SITE_FRONTEND/dist-public-healthcurve/" "$PUBLIC_SITE_STAGE/"
"$PUBLIC_SITE_RSYNC" -a "$PUBLIC_SITE_DATA/" "$PUBLIC_SITE_STAGE/data/"
cp "$PUBLIC_SITE_REPOSITORY/deploy/public-healthcurve.htaccess" "$PUBLIC_SITE_STAGE/.htaccess"
chmod -R a+rX "$PUBLIC_SITE_STAGE"

UV_CACHE_DIR="$PUBLIC_SITE_GENERATED/uv-cache" uv run python \
  "$PUBLIC_SITE_REPOSITORY/scripts/verify_public_healthcurve_bundle.py" \
  "$PUBLIC_SITE_STAGE"

rm -rf "$PUBLIC_SITE_BUNDLE"
mv "$PUBLIC_SITE_STAGE" "$PUBLIC_SITE_BUNDLE"
PUBLIC_SITE_STAGE=""

PUBLIC_SITE_SSH="ssh -p $PUBLIC_SITE_PORT -i $PUBLIC_SITE_IDENTITY -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$PUBLIC_SITE_KNOWN_HOSTS"
PUBLIC_SITE_ARGS=(-av --delay-updates --protocol=31 -e "$PUBLIC_SITE_SSH")
if [[ "$PUBLIC_SITE_DRY_RUN" == "true" ]]; then
  PUBLIC_SITE_ARGS+=(--dry-run)
elif [[ "$PUBLIC_SITE_DRY_RUN" != "false" ]]; then
  echo "public_publish_failed reason=invalid_dry_run_value" >&2
  exit 1
fi

"$PUBLIC_SITE_RSYNC" "${PUBLIC_SITE_ARGS[@]}" \
  "$PUBLIC_SITE_BUNDLE/" "$PUBLIC_SITE_ACCOUNT@$PUBLIC_SITE_HOST:/"

echo "public_publish_succeeded dry_run=$PUBLIC_SITE_DRY_RUN"
