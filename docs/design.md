# ModBoard — Mod Tracker Web Dashboard (Spec)

**Date:** 2026-05-22
**Author:** tiw25
**Status:** Draft — pending user review

## Goal

Self-hosted web dashboard running on home server, used to track mod
development progress and Workshop community interactions across the
author's mods (HoldoutSurvivors, WeaponEnhancement, LifeMilestones,
EazyLife, DayCount).

## Audience

- **Public**: anyone visiting can see mod list, changelog, roadmap,
  subscriber-growth charts, public comments
- **Admin** (author only, login-gated): comment inbox, reply
  composer/tracker, todo board, changelog editor

## Tech Stack (decided)

| Layer | Choice |
|---|---|
| OS | Ubuntu Server (Linux) |
| Source control | **GitHub** — new repo `ModBoard` (separate from each mod's repo) |
| Containerization | **Docker** — `docker-compose` for app + Postgres |
| Backend | Python 3.11+ with FastAPI |
| DB | PostgreSQL 15 (overkill for 500/day but future-proof) |
| ORM | SQLAlchemy 2.x async + Alembic for migrations |
| DB driver | asyncpg |
| Templates | Jinja2 + HTMX (no SPA build pipeline) |
| Charts | Chart.js via CDN |
| Public access | Cloudflare Tunnel (paid plan, author's own domain) |
| Scraper | httpx + BeautifulSoup4 |
| Process manager | Docker Compose (no systemd needed — containers self-restart) |
| Notifications | None — author checks the web UI manually |

## Data Sources (verified live against PZ mods 2026-05-22)

### Steam Web API — `GetPublishedFileDetails` (no key needed)

`POST https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/`
Body: `itemcount=N&publishedfileids[0]=<id>&publishedfileids[1]=<id>...`

**Fields that work (tested)**:
- `title`, `description`, `creator` (SteamID64), `consumer_app_id` (108600 for PZ)
- `file_size`, `preview_url`, `time_created`, `time_updated`
- `subscriptions`, `lifetime_subscriptions`, `favorited`, `views`
- `visibility`, `banned`

**Fields that return EMPTY for PZ mods**:
- `num_comments_public` — use AJAX comment endpoint instead
- `vote_data` — scrape HTML (and PZ items often show "Not enough ratings")
- `tags` — scrape `app_tag` from HTML

**API ↔ displayed label gotcha** — Steam's API field names DON'T match
what the Workshop page shows. Empirical mapping for DayCount (260/13):
- API `subscriptions=260` → page label "Unique Visitors: 260"
- API `favorited=13`     → page label "Current Subscribers: 13"
Display values from HTML scrape, not raw API names.

### Comment thread — AJAX render endpoint (verified working)

`POST https://steamcommunity.com/comment/PublishedFile_Public/render/<creator_steamid>/<file_id>/`
Body: `count=50&start=0&oldestfirst=1`

Returns JSON:
```
{
  "success": true,
  "total_count": <N>,           # USE THIS instead of API's num_comments_public
  "upvotes": <N>,
  "comments_html": "<html>...",
  "timelastpost": <unix>
}
```

`comments_html` is a string of HTML — each comment is:
```html
<div class="commentthread_comment ..." id="comment_<COMMENT_ID>">
  ...
  <a href="https://steamcommunity.com/profiles/<STEAMID>"
     data-miniprofile="<MINIPROFILE>"><bdi>NAME</bdi></a>
  ...
  <div class="commentthread_comment_timestamp"
       title="20 May, 2026 @ 10:22:04 pm PDT"
       data-timestamp="1779340924">
    20 May @ 10:22pm
  </div>
  <div class="commentthread_comment_text" id="comment_content_<ID>">
    BODY HTML (line breaks as &lt;br&gt;)
  </div>
</div>
```

Parse with BeautifulSoup: `soup.select("div.commentthread_comment[id^=comment_]")`.

### Workshop page HTML scrape (for display labels + tags)

`GET https://steamcommunity.com/sharedfiles/filedetails/?id=<id>`

Useful captures:
- "Unique Visitors: N", "Current Subscribers: N", "Current Favorites: N"
- `app_tag` spans
- `fileRatingDetails` block ("Not enough ratings" until ~10 votes)

### Rate limits + ToS

- Steam API: ~100k requests/day, fine for our scale.
- Comment scraping: keep to 1 request per mod per 30 min — Steam tolerates
  personal-use polling. NEVER spin up tight loops.

## Feature Set

### Public pages

| Path | What |
|---|---|
| `/` | Landing — grid of mod cards (name, thumbnail, subs, vote ratio, last update) |
| `/mod/{id}` | Mod detail — description, changelog timeline, roadmap, public todos, screenshots, GitHub link, Workshop link |
| `/mod/{id}/stats` | Growth chart (subs/votes/comments over time) |
| `/mod/{id}/comments` | Public comments mirror (read-only, shows author + body + author's reply if any) |

### Admin pages (login required)

| Path | What |
|---|---|
| `/admin` | Dashboard — total subs across mods, unreplied comment count, recent activity |
| `/admin/inbox` | Comment inbox — all comments across all mods, filterable by mod / status (unreplied / replied / archived) |
| `/admin/inbox/{comment_id}` | Compose reply — BBCode editor with live char count (≤1000 for Workshop comments), preview, mark-as-replied button |
| `/admin/mod/{id}/edit` | Edit mod metadata (description, GitHub URL, public flag) |
| `/admin/mod/{id}/changelog` | Markdown editor for changelog entries — export to BBCode |
| `/admin/mod/{id}/todos` | Kanban board (TODO / DOING / DONE), each card has md body + priority + public flag |
| `/admin/settings` | Steam API key, refresh interval, mod list management |

## Database Schema

```sql
CREATE TABLE mods (
    id              INTEGER PRIMARY KEY,    -- Steam workshop file id
    name            TEXT NOT NULL,           -- short name (HoldoutSurvivors)
    title           TEXT,                    -- display title
    description     TEXT,
    workshop_url    TEXT,
    github_url      TEXT,
    thumbnail_url   TEXT,
    public          INTEGER DEFAULT 1,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE mod_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mod_id          INTEGER NOT NULL,
    captured_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    subscriptions   INTEGER,
    lifetime_subs   INTEGER,
    favorited       INTEGER,
    views           INTEGER,
    votes_up        INTEGER,
    votes_down      INTEGER,
    comments_count  INTEGER,
    last_updated    TIMESTAMP,
    FOREIGN KEY (mod_id) REFERENCES mods(id)
);

CREATE TABLE comments (
    id              TEXT PRIMARY KEY,        -- Steam comment id from HTML
    mod_id          INTEGER NOT NULL,
    author_name     TEXT,
    author_steamid  TEXT,
    posted_at       TIMESTAMP,
    body            TEXT,
    body_html       TEXT,
    seen_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status          TEXT DEFAULT 'unreplied', -- unreplied / replied / archived
    FOREIGN KEY (mod_id) REFERENCES mods(id)
);

CREATE TABLE replies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id      TEXT,
    body_md         TEXT,                    -- markdown source (for editing)
    body_bbcode     TEXT,                    -- compiled BBCode (for paste)
    drafted_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    posted_at       TIMESTAMP,
    FOREIGN KEY (comment_id) REFERENCES comments(id)
);

CREATE TABLE changelog (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mod_id          INTEGER NOT NULL,
    version         TEXT,
    date            DATE,
    body_md         TEXT,
    public          INTEGER DEFAULT 1,
    FOREIGN KEY (mod_id) REFERENCES mods(id)
);

CREATE TABLE todos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mod_id          INTEGER NOT NULL,
    title           TEXT,
    body_md         TEXT,
    status          TEXT DEFAULT 'todo',     -- todo / doing / done
    priority        INTEGER DEFAULT 0,
    public          INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at    TIMESTAMP,
    FOREIGN KEY (mod_id) REFERENCES mods(id)
);

CREATE TABLE admin_users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Background Worker

Single asyncio task launched on FastAPI startup:

- Every **30 minutes**: for each mod in `mods`:
  - Call `GetPublishedFileDetails`
  - Insert row in `mod_snapshots`
  - If `num_comments_public` increased since last snapshot, trigger
    comment scrape
- Comment scrape:
  - Fetch comments thread HTML
  - Parse with BeautifulSoup
  - For each comment with id NOT in `comments` table, insert
- All errors logged + retried on next tick (no crashing)

## Authentication

- Admin login via username/password (bcrypt-hashed in `admin_users`)
- Session via signed cookie (FastAPI's `SessionMiddleware`)
- Single admin user (tiw25) bootstrapped on first run via CLI command
  or env var

## Deployment Plan

1. **Server**: Ubuntu Server (home network)
2. **Docker Compose** orchestrates 3 services:
   - `app` — FastAPI + uvicorn (built from project Dockerfile)
   - `db` — `postgres:15-alpine`
   - `cloudflared` — Cloudflare Tunnel daemon (paid plan, own domain)
3. **Compose file** lives in repo root + a `.env` for secrets
   (DB password, Steam API key, admin password, tunnel token)
4. **HTTPS** automatic via Cloudflare (no certbot needed)
5. **Backup**: a 4th compose service `db-backup` running a cron image
   that does `pg_dump --format=custom` nightly to a host-mounted
   `./backups/` folder. Retain last 14 days.
6. **Updates**: `git pull && docker compose up -d --build` — that's the
   whole release process.

### File layout

```
ModBoard/                 (git repo)
├── app/
│   ├── main.py
│   ├── routes/
│   ├── models/
│   ├── services/         (steam_api, scraper, bbcode)
│   ├── templates/
│   ├── static/
│   └── tests/
├── alembic/              (DB migrations)
├── docker-compose.yml
├── docker-compose.prod.yml
├── Dockerfile
├── pyproject.toml
├── .env.example
├── .gitignore
├── README.md
└── docs/
    └── deployment.md
```

## Implementation Phases (~4 weekends total)

### Phase 1 — MVP (1 weekend)
- FastAPI skeleton + SQLite + Jinja templates
- Mod CRUD admin pages
- Steam API poller (every 30 min)
- Public mod list + mod detail page
- Subscriber chart (single mod, single line)

### Phase 2 — Comment tracking (1 weekend)
- Comment scraper
- Admin inbox with filters
- Reply composer (BBCode + char counter + clipboard copy)
- Mark-as-replied workflow

### Phase 3 — Content management (1 weekend)
- Changelog markdown editor + public timeline view
- Todo Kanban (admin) + public roadmap view
- Auth (single admin user, bcrypt + signed cookie)

### Phase 4 — Polish (1 weekend)
- Cloudflare Tunnel setup
- Mobile-responsive layout
- Multi-mod charts on dashboard
- Backup cron
- Documentation

## Out of Scope (for now)

- Multi-author support
- Webhook notifications (user chose to check manually)
- Comment posting from the dashboard (Steam doesn't expose a write API
  — must still copy/paste to Steam)
- Auto-translation of comments

## Known mods (Steam Workshop IDs)

| Mod | Workshop ID | Status |
|---|---|---|
| Weapon Enhancement | 3721500094 | Published — 126 subs, 22 favs (2026-05-22) |
| LifeMilestones     | 3721918079 | Published — 4 comments (verified parse) |
| DayCount           | 3724689682 | Published |
| HoldoutSurvivors   | (pending)  | In development, awaiting publish |
| EazyLife           | (pending)  | Awaiting publish |

Author SteamID64: `76561198279237042` (used in comment AJAX URL).

## Database Schema (PostgreSQL flavor)

Use PostgreSQL types where helpful — `SERIAL` for autoincrement
primary keys, `JSONB` for any future flexible blob storage,
`TIMESTAMPTZ` for timezone-aware times. Schema below stays SQL-92
where possible so the SQLAlchemy ORM definitions can target either
SQLite (dev) or PostgreSQL (prod) without changes.

```sql
-- (See same column list as before — IDs become SERIAL, timestamps
--  become TIMESTAMPTZ DEFAULT NOW())
```

## Backup Strategy (PostgreSQL specific)

- Nightly `pg_dump --format=custom modboard > /backups/modboard_$(date +%F).dump`
- Keep last 14 days, rotate via cron
- Optional: pgBackRest if WAL-level point-in-time recovery is wanted
  later (not needed at this scale)

## Settled decisions (from 2026-05-22 brainstorm)

- [x] Audience: Public + Admin
- [x] Public access: Cloudflare Tunnel (paid plan, own domain)
- [x] Notifications: none — manual web check
- [x] Backend: Python + FastAPI
- [x] Database: PostgreSQL 15 via SQLAlchemy async (asyncpg)
- [x] Server: Ubuntu Server
- [x] Containerized: Docker + docker-compose (app + db + cloudflared + backup cron)
- [x] Source control: new GitHub repo `ModBoard`
- [x] Workshop IDs: 3 verified, HS + EazyLife pending publish
- [x] Steam API: GetPublishedFileDetails (no key) + AJAX comment render
      endpoint verified live
- [x] HTML scrape for: display labels (Visitors/Subs/Favs), tags, vote

## Related

- Mod Workshop IDs: see [[project_workshop_structure]]
- BBCode notes: see Comment_reply/LifeMilestones_2026-05-22.txt
- Mods Dev hotkey ledger (avoid conflicts): [[reference-mod-hotkeys]]
