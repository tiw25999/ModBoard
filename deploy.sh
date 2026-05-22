#!/usr/bin/env bash
# 0-downtime rolling deploy.
#
# Pipeline:
#   1. git pull
#   2. build new modboard-app image (tagging old one as :previous for rollback)
#   3. run alembic migrations from a one-off container (MUST be backward-compatible)
#   4. recreate app2 → wait for healthy
#   5. recreate app1 → wait for healthy
#   6. caddy reload (cheap, graceful)
#   7. external smoke test
#
# Usage:  bash deploy.sh            (deploy current branch)
#         bash deploy.sh main       (checkout main first)
#
# Rolling-deploy contract (READ THIS BEFORE BREAKING IT):
#   For a brief window (~30s) BOTH old and new code run side-by-side.
#   So every migration must:
#     - add new columns as NULLABLE first (drop NOT NULL constraint to a later deploy)
#     - never DROP a column that the running code still reads
#     - never RENAME — do add+backfill+remove across 2 deploys
#   If you need a breaking schema change, take downtime explicitly:
#     docker compose stop app1 app2 && alembic upgrade head && deploy.sh

set -euo pipefail

cd "$(dirname "$0")"

BRANCH="${1:-}"

cyan()  { printf '\033[1;36m%s\033[0m\n' "$*"; }
green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
red()   { printf '\033[1;31m%s\033[0m\n' "$*" >&2; }

# ---------- 1. git pull --------------------------------------------------
cyan "→ Pulling latest from origin..."
git fetch --quiet origin
if [[ -n "$BRANCH" ]]; then
	git checkout "$BRANCH"
fi
git pull --ff-only

NEW_COMMIT=$(git rev-parse --short HEAD)
cyan "→ Deploying commit $NEW_COMMIT"

# ---------- 2. build (tag previous for rollback) ------------------------
if docker image inspect modboard-app:latest > /dev/null 2>&1; then
	cyan "→ Tagging current image as :previous (for rollback)..."
	docker tag modboard-app:latest modboard-app:previous
fi

cyan "→ Building app image..."
docker compose build app1

# ---------- 3. migrations -----------------------------------------------
cyan "→ Applying DB migrations (one-off container)..."
if ! docker compose run --rm --no-deps app1 alembic upgrade head; then
	red "Migration failed — aborting before touching live containers."
	exit 1
fi

# ---------- 4-5. rolling restart ----------------------------------------
wait_healthy() {
	local name="$1"
	local cid
	cid=$(docker compose ps -q "$name")
	if [[ -z "$cid" ]]; then
		red "$name: container not found"
		return 1
	fi
	for i in $(seq 1 60); do
		local status
		status=$(docker inspect --format='{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "missing")
		case "$status" in
			healthy)   echo "    ${name} healthy after ${i}s"; return 0 ;;
			unhealthy) red "    ${name} reports UNHEALTHY"; return 1 ;;
			missing)   red "    ${name} container vanished"; return 1 ;;
		esac
		sleep 1
	done
	red "    ${name} did not become healthy within 60s"
	return 1
}

roll() {
	local name="$1"
	cyan "→ Recreating $name..."
	docker compose up -d --no-deps --force-recreate "$name"
	if ! wait_healthy "$name"; then
		red "Rolling deploy failed on $name."
		red "Old version of the OTHER replica is still serving traffic."
		red "Run ./rollback.sh to revert both replicas to :previous image."
		exit 1
	fi
}

# Roll standby first (it doesn't run the poller — safer to verify on)
roll app2
roll app1

# ---------- 6. caddy reload (cheap, makes sure config is fresh) ---------
cyan "→ Reloading Caddy..."
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>/dev/null || true

# ---------- 7. external smoke test --------------------------------------
cyan "→ Smoke-test /health via caddy..."
sleep 1
if ! curl -fsS http://127.0.0.1:8000/health > /dev/null; then
	red "Local /health failed. Check 'docker compose logs --tail 80 caddy app1 app2'"
	exit 1
fi

# Best-effort public probe (skip silently if no internet)
if curl -fsS --max-time 5 https://workshopmods.org/health > /dev/null 2>&1; then
	green "✓ Public /health OK"
fi

# ---------- done ---------------------------------------------------------
green ""
green "✓ Deploy complete — commit $NEW_COMMIT"
git --no-pager log -1 --pretty=format:'    %h  %s%n    %an, %ar'
echo ""
