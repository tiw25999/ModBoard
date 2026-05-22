# ModBoard

Self-hosted dashboard tracking Steam Workshop stats and comments for
the author's Project Zomboid mods (Weapon Enhancement, LifeMilestones,
DayCount, HoldoutSurvivors, EazyLife).

**Status: Phase 1 in-progress.** The repo contains the project skeleton
+ Docker stack + ORM models. Remaining tasks: Alembic init, Steam API
client, scraper, poller, public/admin routes, growth chart. See
[docs/phase1-plan.md](docs/phase1-plan.md) for the step-by-step.

## What's already wired

- FastAPI app skeleton with `/health` endpoint (`app/main.py`)
- Pydantic-settings config loader (`app/config.py`)
- SQLAlchemy 2.x async engine + session (`app/db.py`)
- ORM models: `Mod`, `ModSnapshot`, `AdminUser` (`app/models/`)
- Dockerfile + `docker-compose.yml` (app + Postgres 15 + Cloudflared +
  nightly `pg_dump` backup)
- `docker-compose.dev.yml` override (exposes 8000, mounts code volumes,
  disables cloudflared)
- `.env.example` template

## Not yet wired (work-in-progress)

Tasks 4-12 of the phase-1 plan:
- `alembic/` migration tree + initial migration
- `app/services/steam_api.py` — `GetPublishedFileDetails` async client
- `app/services/workshop_scrape.py` — HTML scraper + comment AJAX
- `app/services/poller.py` — asyncio background task
- `app/routes/public.py` — `/`, `/mod/{id}`, `/mod/{id}/stats`
- `app/routes/admin.py` + `app/services/auth.py` — HTTP Basic admin CRUD
- Jinja templates + `app/static/style.css`
- Chart.js stats page

## Local dev (after Docker is installed)

```bash
git clone https://github.com/<your-user>/ModBoard.git
cd ModBoard
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD, SESSION_SECRET, ADMIN_PASSWORD

docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Open `http://localhost:8000/health` — expect `{"status":"ok"}`.

## Production deploy (Ubuntu + Cloudflare Tunnel)

```bash
git clone https://github.com/<your-user>/ModBoard.git
cd ModBoard
cp .env.example .env
# edit .env: set ALL passwords + CLOUDFLARED_TOKEN from Zero Trust dashboard

docker compose up -d --build
docker compose logs -f cloudflared   # confirm tunnel registered
```

Point a Cloudflare Zero Trust public hostname at `http://app:8000`.

## Tech stack

| Layer | Choice |
|---|---|
| OS | Ubuntu Server |
| Container | Docker + docker-compose |
| Backend | Python 3.11 + FastAPI |
| DB | PostgreSQL 15 + asyncpg + SQLAlchemy 2.x async |
| Migrations | Alembic |
| Templates | Jinja2 + HTMX |
| Charts | Chart.js (CDN) |
| Scraper | httpx + BeautifulSoup4 + lxml |
| Public access | Cloudflare Tunnel |

## Docs

- [docs/design.md](docs/design.md) — full design spec
- [docs/phase1-plan.md](docs/phase1-plan.md) — Phase 1 implementation plan with task-by-task code

## License

Personal project — no license declared yet.
