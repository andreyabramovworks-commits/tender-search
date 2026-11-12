#!/usr/bin/env bash
# A manager sync is deliberately independent from deployment.  It only yields
# to the priority daily collection and is requested again by the collection's
# ExecStopPost as soon as that collection has finished.
set -Eeuo pipefail
ROOT=/opt/tender-search
exec 9>"$ROOT/shared/daily-run.lock"
flock -n 9 || exit 0
# This is an activity probe, not a lock around the lightweight sync itself:
# a new daily collection may always begin immediately.
flock -u 9
export RELEASE_SHA="$(cat "$ROOT/shared/deployed-sha")" TENDER_ROOT="$ROOT"
cd "$ROOT/current"
exec docker compose --env-file "$ROOT/shared/.env" -p tender-search run --rm --no-deps app tender-search sync-manager-ai
