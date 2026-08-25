#!/usr/bin/env bash
set -euo pipefail

HC_SCHEDULED_REPOSITORY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
HC_SCHEDULED_FRONTEND="$HC_SCHEDULED_REPOSITORY/frontend"
HC_SCHEDULED_STATE="$HC_SCHEDULED_REPOSITORY/var/public-healthcurve"
HC_SCHEDULED_LOCK_HASH="$HC_SCHEDULED_STATE/package-lock.sha256"
mkdir -p "$HC_SCHEDULED_STATE"

git -C "$HC_SCHEDULED_REPOSITORY" pull --ff-only origin main

HC_SCHEDULED_CURRENT_HASH="$(shasum -a 256 "$HC_SCHEDULED_FRONTEND/package-lock.json" | awk '{print $1}')"
HC_SCHEDULED_INSTALLED_HASH=""
if [[ -f "$HC_SCHEDULED_LOCK_HASH" ]]; then
  HC_SCHEDULED_INSTALLED_HASH="$(<"$HC_SCHEDULED_LOCK_HASH")"
fi
if [[ ! -d "$HC_SCHEDULED_FRONTEND/node_modules" || "$HC_SCHEDULED_INSTALLED_HASH" != "$HC_SCHEDULED_CURRENT_HASH" ]]; then
  npm --prefix "$HC_SCHEDULED_FRONTEND" ci
  printf '%s\n' "$HC_SCHEDULED_CURRENT_HASH" > "$HC_SCHEDULED_LOCK_HASH"
fi

exec "$HC_SCHEDULED_REPOSITORY/scripts/publish_public_healthcurve.sh"
