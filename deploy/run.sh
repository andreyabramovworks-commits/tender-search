#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=/opt/tender-search
exec 9>"$ROOT/shared/operation.lock"
flock -n 9 || { echo 'Another run/deployment is active'; exit 1; }
export RELEASE_SHA="$(cat "$ROOT/shared/deployed-sha")" TENDER_ROOT="$ROOT"
cd "$ROOT/current"
if [[ $# -eq 0 ]]; then set -- run; fi
exec docker compose --env-file "$ROOT/shared/.env" -p tender-search run --rm --no-deps app tender-search "$@"
