#!/usr/bin/env bash
# Archive queue: deliberately independent of the fast daily tender search.
set -Eeuo pipefail
ROOT=/opt/tender-search
exec 9>"$ROOT/shared/document-archive.lock"
flock -n 9 || exit 0
export RELEASE_SHA="$(cat "$ROOT/shared/deployed-sha")" TENDER_ROOT="$ROOT"
cd "$ROOT/current"
# Archive every pending accessible document in one run.  The process remains
# serialized by this flock and the database advisory lock.
exec docker compose --env-file "$ROOT/shared/.env" -p tender-search run --rm --no-deps app \
  tender-search archive-documents
