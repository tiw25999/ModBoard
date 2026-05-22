# Production deploy guide

How to put ModBoard on the internet behind a Cloudflare Tunnel. Assumes
you already have a working `docker compose up` locally.

## Prerequisites

- A Linux server (Ubuntu 22.04+ tested). 1 vCPU / 1 GB RAM works for
  3-5 mods; bump to 2 GB if you expect heavy forum activity.
- A domain you control on Cloudflare.
- Docker + docker-compose installed on the server.
- Google OAuth client (optional, for sign-in) — see
  `app/routes/auth.py` for setup.

## 1. Cloudflare Tunnel

1. Cloudflare dashboard → **Zero Trust** → **Networks** → **Tunnels** →
   **Create a tunnel**.
2. Name it `modboard` → save → copy the **tunnel token** (long string
   starting with `eyJ...`).
3. Skip the install step — our docker-compose already runs cloudflared
   for you.
4. Under **Public hostname** → **Add**:
   - Subdomain: `modboard` (or whatever)
   - Domain: pick your domain
   - Service: `HTTP` → `app:8000`
   - Save.

## 2. Server-side .env

On the server, `cp .env.example .env` then edit:

```bash
SESSION_SECRET=<generate with: python -c "import secrets; print(secrets.token_urlsafe(48))">
ADMIN_USERNAME=<your admin username>
ADMIN_PASSWORD=<strong unique password>
POSTGRES_PASSWORD=<strong unique>
DATABASE_URL=postgresql+asyncpg://modboard:<that-same-password>@db:5432/modboard

# Production switch — flips all cookies to Secure (HTTPS only)
PRODUCTION=true

# Cloudflare token from step 1
CLOUDFLARED_TOKEN=eyJ...

# If using Google OAuth, point redirect at your public URL:
GOOGLE_REDIRECT_URI=https://modboard.yourdomain.tld/auth/google/callback
```

Then in Google Cloud → APIs & Services → Credentials → your OAuth
client → **Authorized redirect URIs**: add the same URL.

## 3. Bring up the stack

```bash
docker compose up -d --build
docker compose exec app alembic upgrade head
docker compose logs -f cloudflared   # confirm the tunnel registered
```

Hit `https://modboard.yourdomain.tld/health` — expect `{"status":"ok"}`.

## 4. Bootstrap content

1. `https://your-url/auth/login` → admin form → sign in.
2. `/admin/mods` → add your Workshop IDs.
3. Either wait for the next 30-min poll cycle or click **↻ Poll now**
   on `/admin`.
4. Customize:
   - `/admin/news` — write a launch announcement, tick "Show as
     banner" so visitors see it.
   - `/admin/mods/{id}/roadmap` — fill out the roadmap board for each
     mod.
   - `app/templates/donate.html` — replace `REPLACE_WITH_YOUR_HANDLE`
     placeholders with your real Ko-fi / PayPal / GitHub handles +
     wallet address.

## 5. Backups

`db-backup` container dumps Postgres nightly to `./backups/`. Wire that
folder to off-site storage:

```bash
# Example: rsync to a cheap object store every night
0 4 * * * rsync -az /path/to/ModBoard/backups/ user@offsite:modboard/
```

To restore from a dump:

```bash
docker compose exec -T db pg_restore -U modboard -d modboard --clean \
  < backups/modboard_2026-05-22.dump
```

## 6. Updating the deployed site

```bash
git pull
docker compose build app
docker compose up -d app                  # picks up new image, no downtime for db
docker compose exec app alembic upgrade head   # if any new migrations
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/auth/login` form posts but you never get logged in | `PRODUCTION=true` but you're hitting HTTP, so the Secure cookie is dropped | Make sure traffic is HTTPS (Cloudflare Tunnel does this automatically; check `https://` in URL bar) |
| Poller logs `429 Too Many Requests` from steamcommunity | Steam rate-limited the server IP | Wait 30-60 min; poller already staggers 2s between mods |
| Email/notifications never arrive | SMTP isn't wired yet — only in-site notifications work | See `docs/phase1-plan.md` Phase 6e for SMTP provider options |
| Google sign-in says `redirect_uri_mismatch` | The URI in Google Cloud doesn't match `GOOGLE_REDIRECT_URI` exactly | Copy them character-for-character; trailing slash matters |
