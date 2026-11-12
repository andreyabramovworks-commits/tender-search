#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=/opt/tender-search
# The daily collection is the priority job.  It must never be blocked by the
# manager-copy sync or a release check; only another collection may exclude it.
exec 9>"$ROOT/shared/daily-run.lock"
flock -n 9 || { echo 'Another TenderLand collection is active'; exit 1; }
export RELEASE_SHA="$(cat "$ROOT/shared/deployed-sha")" TENDER_ROOT="$ROOT"
cd "$ROOT/current"
if [[ $# -eq 0 ]]; then set -- run; fi
exec docker compose --env-file "$ROOT/shared/.env" -p tender-search run --rm --no-deps app tender-search "$@"
