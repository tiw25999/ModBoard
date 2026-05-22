#!/usr/bin/env bash
# Roll back to the previously-deployed image (saved as :previous by
# deploy.sh). Does a rolling restart so the rollback itself stays
# 0-downtime too.
#
# This does NOT touch git or the DB. If you need to roll back a
# breaking migration, you'll have to do that manually:
#   docker compose run --rm app1 alembic downgrade -1

set -euo pipefail
cd "$(dirname "$0")"

cyan()  { printf '\033[1;36m%s\033[0m\n' "$*"; }
green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
red()   { printf '\033[1;31m%s\033[0m\n' "$*" >&2; }

if ! docker image inspect modboard-app:previous > /dev/null 2>&1; then
	red "No modboard-app:previous image to roll back to."
	red "(deploy.sh tags the prior image on every build — this means you've"
	red " either never deployed twice, or the previous tag was wiped.)"
	exit 1
fi

cyan "→ Reverting modboard-app:latest → :previous..."
docker tag modboard-app:previous modboard-app:latest

wait_healthy() {
	local name="$1" cid
	cid=$(docker compose ps -q "$name")
	for i in $(seq 1 60); do
		local status
		status=$(docker inspect --format='{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "missing")
		[[ "$status" == "healthy" ]] && { echo "    ${name} healthy after ${i}s"; return 0; }
		[[ "$status" == "unhealthy" ]] && { red "    ${name} UNHEALTHY"; return 1; }
		sleep 1
	done
	red "    ${name} did not become healthy within 60s"
	return 1
}

for name in app2 app1; do
	cyan "→ Recreating $name with previous image..."
	docker compose up -d --no-deps --force-recreate "$name"
	wait_healthy "$name" || { red "Rollback failed on $name."; exit 1; }
done

cyan "→ Reloading Caddy..."
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>/dev/null || true

if ! curl -fsS http://127.0.0.1:8000/health > /dev/null; then
	red "Health check failed after rollback. Inspect logs."
	exit 1
fi

green "✓ Rolled back to previous image."
