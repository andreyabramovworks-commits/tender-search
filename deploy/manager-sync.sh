#!/usr/bin/env bash
# Non-blocking manager-copy sync: a daily collection may be running, but that
# must not turn this lightweight periodic replication into a failed unit.
set -Eeuo pipefail
ROOT=/opt/tender-search
exec 9>"$ROOT/shared/operation.lock"
flock -n 9 || exit 0
export RELEASE_SHA="$(cat "$ROOT/shared/deployed-sha")" TENDER_ROOT="$ROOT"
cd "$ROOT/current"
exec docker compose --env-file "$ROOT/shared/.env" -p tender-search run --rm --no-deps app tender-search sync-manager-ai
