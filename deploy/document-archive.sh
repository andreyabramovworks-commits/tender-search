#!/usr/bin/env bash
# Archive queue: deliberately independent of the fast daily tender search.
set -Eeuo pipefail
ROOT=/opt/tender-search
exec 9>"$ROOT/shared/document-archive.lock"
flock -n 9 || exit 0
export RELEASE_SHA="$(cat "$ROOT/shared/deployed-sha")" TENDER_ROOT="$ROOT"
cd "$ROOT/current"
# One controlled batch per run: it protects source platforms and Google Drive
# while letting the timer steadily empty the queue during the day.
exec docker compose --env-file "$ROOT/shared/.env" -p tender-search run --rm --no-deps app \
  tender-search archive-documents --limit 20
