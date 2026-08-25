#!/usr/bin/env bash
set -euo pipefail

HC_PUBLISH_REPOSITORY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
HC_PUBLISH_FRONTEND="$HC_PUBLISH_REPOSITORY/frontend"
HC_PUBLISH_GENERATED="$HC_PUBLISH_REPOSITORY/var/public-healthcurve"
HC_PUBLISH_DATA="$HC_PUBLISH_GENERATED/data"
HC_PUBLISH_BUNDLE="$HC_PUBLISH_GENERATED/bundle"
mkdir -p "$HC_PUBLISH_GENERATED"
HC_PUBLISH_STAGE="$(mktemp -d "$HC_PUBLISH_GENERATED/.bundle-stage.XXXXXX")"
HC_PUBLISH_RSYNC="${HC_PUBLIC_HEALTHCURVE_RSYNC:-/opt/homebrew/bin/rsync}"
HC_PUBLISH_HOST="${HC_PUBLIC_HEALTHCURVE_HOST:-jeffellin.com}"
HC_PUBLISH_ACCOUNT="${HC_PUBLIC_HEALTHCURVE_ACCOUNT:-jellin2}"
HC_PUBLISH_PORT="${HC_PUBLIC_HEALTHCURVE_PORT:-22}"
HC_PUBLISH_IDENTITY="${HC_PUBLIC_HEALTHCURVE_IDENTITY:-/Users/jeff/.ssh/healthcurve_public_deploy}"
HC_PUBLISH_KNOWN_HOSTS="${HC_PUBLIC_HEALTHCURVE_KNOWN_HOSTS:-/Users/jeff/.ssh/known_hosts}"
HC_PUBLISH_EXPECTED_HOST_KEY="${HC_PUBLIC_HEALTHCURVE_HOST_KEY_SHA256:-SHA256:IuO3r8KWrx3xcCM3nVBWk2eNvbFIKI0exWIJrrtd76Y}"
HC_PUBLISH_DRY_RUN="${HC_PUBLIC_HEALTHCURVE_DRY_RUN:-false}"

cleanup() {
  if [[ -d "$HC_PUBLISH_STAGE" ]]; then
    rm -r "$HC_PUBLISH_STAGE"
  fi
}
trap cleanup EXIT

if [[ ! -x "$HC_PUBLISH_RSYNC" ]]; then
  echo "public_publish_failed reason=current_rsync_missing" >&2
  exit 1
fi
if [[ ! -f "$HC_PUBLISH_IDENTITY" || ! -f "$HC_PUBLISH_KNOWN_HOSTS" ]]; then
  echo "public_publish_failed reason=ssh_material_missing" >&2
  exit 1
fi

HC_PUBLISH_OBSERVED_HOST_KEY="$({
  ssh-keygen -F "$HC_PUBLISH_HOST" -f "$HC_PUBLISH_KNOWN_HOSTS" 2>/dev/null \
    | ssh-keygen -lf - -E sha256 2>/dev/null \
    | awk '$1 == "256" && $4 == "(ED25519)" {print $2; exit}'
} || true)"
if [[ "$HC_PUBLISH_OBSERVED_HOST_KEY" != "$HC_PUBLISH_EXPECTED_HOST_KEY" ]]; then
  echo "public_publish_failed reason=ssh_host_key_mismatch" >&2
  exit 1
fi

npm --prefix "$HC_PUBLISH_FRONTEND" run build:public-healthcurve

docker compose \
  --project-directory "$HC_PUBLISH_REPOSITORY" \
  -f "$HC_PUBLISH_REPOSITORY/docker-compose.yml" \
  run --rm --no-deps \
  -v "$HC_PUBLISH_REPOSITORY/src:/app/src:ro" \
  -v "$HC_PUBLISH_GENERATED:/output" \
  api python -m healthcurve.public_site --output /output/data

"$HC_PUBLISH_RSYNC" -a "$HC_PUBLISH_FRONTEND/dist-public-healthcurve/" "$HC_PUBLISH_STAGE/"
"$HC_PUBLISH_RSYNC" -a "$HC_PUBLISH_DATA/" "$HC_PUBLISH_STAGE/data/"
cp "$HC_PUBLISH_REPOSITORY/deploy/public-healthcurve.htaccess" "$HC_PUBLISH_STAGE/.htaccess"

UV_CACHE_DIR="$HC_PUBLISH_GENERATED/uv-cache" uv run python \
  "$HC_PUBLISH_REPOSITORY/scripts/verify_public_healthcurve_bundle.py" \
  "$HC_PUBLISH_STAGE"

rm -rf "$HC_PUBLISH_BUNDLE"
mv "$HC_PUBLISH_STAGE" "$HC_PUBLISH_BUNDLE"
HC_PUBLISH_STAGE=""

HC_PUBLISH_SSH="ssh -p $HC_PUBLISH_PORT -i $HC_PUBLISH_IDENTITY -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$HC_PUBLISH_KNOWN_HOSTS"
HC_PUBLISH_ARGS=(-av --delay-updates --protocol=31 -e "$HC_PUBLISH_SSH")
if [[ "$HC_PUBLISH_DRY_RUN" == "true" ]]; then
  HC_PUBLISH_ARGS+=(--dry-run)
elif [[ "$HC_PUBLISH_DRY_RUN" != "false" ]]; then
  echo "public_publish_failed reason=invalid_dry_run_value" >&2
  exit 1
fi

"$HC_PUBLISH_RSYNC" "${HC_PUBLISH_ARGS[@]}" \
  "$HC_PUBLISH_BUNDLE/" "$HC_PUBLISH_ACCOUNT@$HC_PUBLISH_HOST:/"

echo "public_publish_succeeded dry_run=$HC_PUBLISH_DRY_RUN"
