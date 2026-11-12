#!/usr/bin/env bash
# Weekly reconciliation is intentionally outside the 05:00 daily window.
set -Eeuo pipefail
ROOT=/opt/tender-search
exec 9>"$ROOT/shared/deep-run.lock"
flock -n 9 || exit 0
export RELEASE_SHA="$(cat "$ROOT/shared/deployed-sha")" TENDER_ROOT="$ROOT"
cd "$ROOT/current"
exec docker compose --env-file "$ROOT/shared/.env" -p tender-search run --rm --no-deps app \
  tender-search run --deep
