# Zero-Downtime Deploy

## Architecture

```
Internet → Cloudflare Tunnel (cloudflared)
                  │
                  ▼
            Caddy reverse proxy  ── round-robin + health checks
              │             │
              ▼             ▼
           app1          app2
        (poller on)   (poller off)
              │             │
              └──────┬──────┘
                     ▼
                Postgres (db)
```

Both `app1` and `app2` run the **same image** (`modboard-app:latest`).
Caddy load-balances between them; when one is being recreated during
a deploy, Caddy detects `/health` failing within ~3s and routes 100%
of traffic to the other replica.

Only `app1` runs the Steam poller (controlled by `RUN_POLLER` env)
so we don't double the Steam API rate.

---

## One-time migration from the old single-`app` setup

If you're upgrading a server that currently runs the old compose
(single `app` service, no caddy), do this once:

```bash
cd /home/tew/projects/ModBoard
git pull
docker compose up -d --build
```

That's it. **No Cloudflare dashboard change needed** — the `caddy`
service declares a docker-network alias `app`, so the tunnel's
existing target `http://app:8000` keeps working, but `app` now
resolves to caddy (which round-robins to app1/app2).

After the cutover, every `./deploy.sh` is zero-downtime.

---

## Normal deploy (every day after the first)

```bash
ssh tew@server
cd /home/tew/projects/ModBoard
./deploy.sh
```

What it does:

| Step | What | Downtime? |
|------|------|-----------|
| 1 | `git pull` | none |
| 2 | Tag current image as `:previous` (rollback safety) | none |
| 3 | Build new image | none — old containers still serving |
| 4 | Run Alembic migrations from a temp container | none — migrations must be backward-compatible (see below) |
| 5 | Recreate `app2`, wait for `healthy` | none — `app1` serves all traffic |
| 6 | Recreate `app1`, wait for `healthy` | none — `app2` serves all traffic |
| 7 | Reload Caddy | none — graceful |
| 8 | Smoke-test `/health` | — |

Caddy's active health probe runs every 3s. When `app2` goes down
during recreate, Caddy pulls it out of rotation within one probe
cycle. New requests flow to `app1` only. When `app2` comes back
healthy, it rejoins the pool.

---

## Backward-compatible migration rules

During the brief window when one new and one old container both
run, the **schema must satisfy both**. So:

| Migration type | Safe? | How to do it 0-downtime |
|----|----|----|
| Add nullable column | ✅ | Just add it |
| Add NOT NULL column | ⚠ | Two deploys: (1) add nullable + backfill (2) tighten to NOT NULL |
| Drop column | ⚠ | Two deploys: (1) ship code that doesn't read it (2) drop |
| Rename column | ❌ | Add new + dual-write + read from new + drop old → 3 deploys |
| Add index | ✅ | Use `CREATE INDEX CONCURRENTLY` (wrap in `with op.get_context().autocommit_block():`) |
| Add table | ✅ | Just add it |
| Add FK | ⚠ | Two deploys if the FK target also changes |

**If you need a breaking schema change**, take downtime explicitly:

```bash
docker compose stop app1 app2
docker compose run --rm app1 alembic upgrade head
./deploy.sh
```

---

## Rollback

```bash
./rollback.sh
```

Restores `modboard-app:latest` to the previous image tag (saved
automatically by `deploy.sh` on every build) and does a rolling
restart. Also zero-downtime.

Does **not** roll back migrations — for that:

```bash
docker compose run --rm app1 alembic downgrade -1
```

(Only safe if the new code didn't already write data in a shape
the old schema can't represent.)

---

## Verifying it actually is zero-downtime

In one terminal during deploy:

```bash
while true; do
  curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" \
    https://workshopmods.org/health
  sleep 0.5
done
```

Run `./deploy.sh` in another terminal. The probe should stay `200`
the whole time. Any `502` or timeout means Caddy didn't catch the
upstream going down fast enough — investigate by inspecting:

```bash
docker compose logs --tail 100 caddy
docker compose logs --tail 100 app1 app2
```

---

## Troubleshooting

### `wait_healthy` times out on app1/app2

The container probably can't reach the DB or is crashing on startup.

```bash
docker compose logs --tail 80 app1
```

Common causes: bad `.env`, alembic migration left things half-applied,
new code raises at import time.

### Caddy returns 502 immediately

Both replicas are unhealthy.

```bash
docker compose ps                       # see who's healthy
docker compose exec caddy wget -qO- http://app1:8000/health
docker compose exec caddy wget -qO- http://app2:8000/health
```

### Cloudflare Tunnel returns 502 but local works

cloudflared can't resolve `app` (or `caddy`) inside the docker
network. Check:

```bash
docker compose exec cloudflared wget -qO- http://app:8000/health
```

Should print `{"status":"ok"}`. If it fails, the caddy service
isn't up, isn't healthy, or doesn't have the `app` network alias
declared in compose.
