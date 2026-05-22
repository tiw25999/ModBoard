#!/usr/bin/env bash
# Deploy script — pull latest, rebuild app image, apply migrations.
# Idempotent + safe to re-run.
#
# Usage:  bash deploy.sh            (deploy from current branch)
#         bash deploy.sh main       (force-checkout main first)
#
# Exits non-zero on any failure so you can wire it into cron / CI later.
set -euo pipefail

BRANCH="${1:-}"
cd "$(dirname "$0")"

echo "→ Pulling latest from origin..."
git fetch --quiet origin
if [[ -n "$BRANCH" ]]; then
  git checkout "$BRANCH"
fi
git pull --ff-only

echo "→ Rebuilding app image..."
docker compose build app

echo "→ Recreating app container (db + cloudflared stay up)..."
docker compose up -d app

echo "→ Applying database migrations..."
docker compose exec -T app alembic upgrade head

echo "→ Smoke-checking /health..."
sleep 3
if ! curl -sf http://localhost:8000/health > /dev/null; then
  echo "✗ /health failed after deploy — check 'docker compose logs --tail 50 app'"
  exit 1
fi

echo ""
echo "✓ Deploy complete. Latest commit:"
git --no-pager log -1 --pretty=format:'  %h  %s%n  %an, %ar'
echo ""
