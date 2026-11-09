#!/usr/bin/env bash
# Installed in /opt/tender-search/bin after review. Poll only CI-approved production.
set -Eeuo pipefail
ROOT=/opt/tender-search
REPO=https://github.com/andreyabramovworks-commits/tender-search.git
exec 9>"$ROOT/shared/operation.lock"
flock -n 9 || exit 0
if [[ ! -d "$ROOT/repo.git" ]]; then
  git clone --bare "$REPO" "$ROOT/repo.git"
fi
git --git-dir="$ROOT/repo.git" fetch --quiet origin refs/heads/production
sha="$(git --git-dir="$ROOT/repo.git" rev-parse FETCH_HEAD)"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 1
previous="$(cat "$ROOT/shared/deployed-sha" 2>/dev/null || true)"
[[ "$sha" != "$previous" ]] || exit 0
release="$ROOT/releases/$sha"
mkdir -p "$release"
git --git-dir="$ROOT/repo.git" archive "$sha" | tar -x -C "$release"
export RELEASE_SHA="$sha" TENDER_ROOT="$ROOT"
compose=(docker compose --env-file "$ROOT/shared/.env" -p tender-search -f "$release/compose.yaml")
"${compose[@]}" build app
"${compose[@]}" up -d --wait db
# The panel uses an immutable image tag per release. Force only this stateless
# service to be recreated so its buttons always match the deployed CLI.
"${compose[@]}" up -d --force-recreate panel
# Backup before migrations; refuse to continue on a failed backup/migration.
mkdir -p "$ROOT/shared/backups"
"${compose[@]}" exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$ROOT/shared/backups/pre-${sha}-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
"${compose[@]}" run --rm --no-deps app alembic upgrade head
"${compose[@]}" run --rm --no-deps app python scripts/check_schema.py
ln -sfn "$release" "$ROOT/current.next"
mv -Tf "$ROOT/current.next" "$ROOT/current"
printf '%s\n' "$sha" > "$ROOT/shared/deployed-sha.next"
mv -f "$ROOT/shared/deployed-sha.next" "$ROOT/shared/deployed-sha"
echo "Deployed $sha"
