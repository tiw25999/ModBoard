# ModBoard Phase 1 MVP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a working web dashboard that polls Steam for the author's 3 published PZ mods every 30 min, stores snapshots in Postgres, and renders a public mod list + per-mod detail page with a subscriber growth chart. Admin can add/edit/delete mods via a basic-auth gated page.

**Architecture:** FastAPI async monolith. Postgres via SQLAlchemy 2.x async + asyncpg. Alembic for migrations. Jinja2 server-side templates + HTMX for interactivity (no SPA). Chart.js via CDN for the growth chart. Background poller runs as an asyncio task in the same process — single-replica deploy, no worker/queue split needed at this scale.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.x async, asyncpg, Alembic, httpx, BeautifulSoup4, Jinja2, Chart.js, Docker + docker-compose, PostgreSQL 15-alpine, Cloudflare Tunnel.

**Spec reference:** [2026-05-22-modboard-design.md](../specs/2026-05-22-modboard-design.md)

---

## File Structure

```
ModBoard/                            (new GitHub repo)
├── app/
│   ├── __init__.py
│   ├── main.py                       FastAPI app factory + lifespan
│   ├── config.py                     settings via pydantic-settings (.env)
│   ├── db.py                         SQLAlchemy async engine + session
│   ├── models/
│   │   ├── __init__.py
│   │   ├── mod.py                    Mod + ModSnapshot ORM
│   │   └── admin.py                  AdminUser ORM
│   ├── services/
│   │   ├── __init__.py
│   │   ├── steam_api.py              GetPublishedFileDetails client
│   │   ├── workshop_scrape.py        HTML scrape for display labels
│   │   ├── poller.py                 asyncio background task
│   │   └── auth.py                   HTTP Basic admin gate
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── public.py                 /, /mod/{id}, /mod/{id}/stats
│   │   └── admin.py                  /admin/mods CRUD
│   ├── templates/
│   │   ├── base.html
│   │   ├── mod_list.html
│   │   ├── mod_detail.html
│   │   ├── mod_stats.html
│   │   └── admin_mods.html
│   └── static/
│       └── style.css                 minimal CSS
├── alembic/
│   ├── env.py                        async-aware migration env
│   ├── script.py.mako
│   └── versions/                     migration files
├── tests/
│   ├── conftest.py
│   ├── test_steam_api.py             hit real API for 3 known mod IDs
│   └── test_models.py
├── Dockerfile
├── docker-compose.yml                app + db + cloudflared + db-backup
├── docker-compose.dev.yml            override: no cloudflared, expose port
├── alembic.ini
├── pyproject.toml                    deps via uv or pip-tools
├── .env.example
├── .gitignore
├── .dockerignore
└── README.md
```

---

### Task 1: Initialize GitHub repo + Python project skeleton

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.dockerignore`, `README.md`, `.env.example`
- Create: `app/__init__.py`, `app/main.py`

- [ ] **Step 1: Create empty GitHub repo**

Run on the dev machine (any Git client):

```bash
gh repo create ModBoard --public --description "Self-hosted dashboard for tracking PZ mod stats and Steam Workshop comments"
git clone https://github.com/tiw25999/ModBoard.git
cd ModBoard
```

- [ ] **Step 2: Write pyproject.toml**

Create `pyproject.toml`:

```toml
[project]
name = "modboard"
version = "0.1.0"
description = "Self-hosted PZ mod tracker"
requires-python = ">=3.11"
dependencies = [
    "fastapi[standard]>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.30",
    "alembic>=1.13",
    "pydantic-settings>=2.6",
    "httpx>=0.28",
    "beautifulsoup4>=4.12",
    "lxml>=5.3",
    "jinja2>=3.1",
    "bcrypt>=4.2",
    "itsdangerous>=2.2",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "httpx>=0.28", "ruff>=0.7"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 3: Write .gitignore**

```gitignore
__pycache__/
*.pyc
.venv/
.env
*.db
backups/
.pytest_cache/
.ruff_cache/
```

- [ ] **Step 4: Write .env.example**

```bash
# App
APP_HOST=0.0.0.0
APP_PORT=8000
SESSION_SECRET=change-me-to-a-long-random-string
ADMIN_USERNAME=tiw25
ADMIN_PASSWORD=change-me

# Database
POSTGRES_USER=modboard
POSTGRES_PASSWORD=change-me
POSTGRES_DB=modboard
DATABASE_URL=postgresql+asyncpg://modboard:change-me@db:5432/modboard

# Steam (creator SteamID64, used in comment AJAX URL)
STEAM_CREATOR_ID=76561198279237042
POLL_INTERVAL_MINUTES=30

# Cloudflare Tunnel
CLOUDFLARED_TOKEN=
```

- [ ] **Step 5: Write minimal FastAPI app**

`app/__init__.py`: empty file.

`app/main.py`:

```python
from fastapi import FastAPI

app = FastAPI(title="ModBoard")

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 6: Local sanity run**

```bash
python -m venv .venv
.venv/Scripts/activate    # or source .venv/bin/activate on Linux
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Open `http://localhost:8000/health` → expect `{"status":"ok"}`.

- [ ] **Step 7: Commit + push**

```bash
git add .
git commit -m "feat: project skeleton + health endpoint"
git push origin main
```

---

### Task 2: Dockerize the app + Postgres

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `docker-compose.dev.yml`

- [ ] **Step 1: Write Dockerfile**

```dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
RUN uv pip install --system -e .

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Write docker-compose.yml (base)**

```yaml
services:
  app:
    build: .
    env_file: .env
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped

  db:
    image: postgres:15-alpine
    env_file: .env
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER}"]
      interval: 5s
      retries: 10
    restart: unless-stopped

  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel --no-autoupdate run --token ${CLOUDFLARED_TOKEN}
    env_file: .env
    depends_on:
      - app
    restart: unless-stopped

  db-backup:
    image: postgres:15-alpine
    env_file: .env
    volumes:
      - ./backups:/backups
    entrypoint: >
      sh -c 'while true; do
        pg_dump -h db -U $$POSTGRES_USER --format=custom $$POSTGRES_DB
          > /backups/modboard_$$(date +%F).dump;
        find /backups -name "modboard_*.dump" -mtime +14 -delete;
        sleep 86400;
      done'
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped

volumes:
  pgdata:
```

- [ ] **Step 3: Write docker-compose.dev.yml (override)**

```yaml
services:
  app:
    ports:
      - "8000:8000"
    volumes:
      - ./app:/app/app
      - ./alembic:/app/alembic
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  cloudflared:
    profiles: ["never"]      # disabled in dev
```

- [ ] **Step 4: Smoke test the stack**

```bash
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD + SESSION_SECRET + ADMIN_PASSWORD to something
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Open `http://localhost:8000/health` → expect `{"status":"ok"}`.
Run `docker compose exec db psql -U modboard -d modboard -c "\dt"` → expect "Did not find any relations" (no tables yet).

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml docker-compose.dev.yml
git commit -m "feat: dockerize app + postgres + cloudflared + db-backup"
git push
```

---

### Task 3: SQLAlchemy models + DB engine

**Files:**
- Create: `app/config.py`, `app/db.py`, `app/models/__init__.py`, `app/models/mod.py`, `app/models/admin.py`
- Modify: `app/main.py`

- [ ] **Step 1: Settings module**

`app/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    session_secret: str
    admin_username: str
    admin_password: str
    steam_creator_id: str
    poll_interval_minutes: int = 30


settings = Settings()
```

- [ ] **Step 2: Async engine + session**

`app/db.py`:

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
```

- [ ] **Step 3: Mod + ModSnapshot models**

`app/models/__init__.py`:

```python
from app.models.admin import AdminUser
from app.models.mod import Mod, ModSnapshot

__all__ = ["AdminUser", "Mod", "ModSnapshot"]
```

`app/models/mod.py`:

```python
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Mod(Base):
    __tablename__ = "mods"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Steam workshop file id
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    workshop_url: Mapped[str | None] = mapped_column(Text)
    github_url: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    public: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    snapshots: Mapped[list["ModSnapshot"]] = relationship(
        back_populates="mod", cascade="all, delete-orphan"
    )


class ModSnapshot(Base):
    __tablename__ = "mod_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mod_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("mods.id", ondelete="CASCADE"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    subscriptions: Mapped[int | None] = mapped_column(Integer)
    lifetime_subs: Mapped[int | None] = mapped_column(Integer)
    favorited: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(Integer)
    comments_count: Mapped[int | None] = mapped_column(Integer)  # from AJAX endpoint
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # HTML-scraped values (canonical for display)
    visitors_display: Mapped[int | None] = mapped_column(Integer)
    subscribers_display: Mapped[int | None] = mapped_column(Integer)
    favorites_display: Mapped[int | None] = mapped_column(Integer)

    mod: Mapped[Mod] = relationship(back_populates="snapshots")
```

`app/models/admin.py`:

```python
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 4: Commit**

```bash
git add app/config.py app/db.py app/models/
git commit -m "feat: settings + async DB engine + Mod/ModSnapshot/AdminUser ORMs"
git push
```

---

### Task 4: Alembic init + first migration

**Files:**
- Create: `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/0001_initial.py`

- [ ] **Step 1: Alembic init**

```bash
alembic init -t async alembic
```

- [ ] **Step 2: Configure alembic/env.py**

Replace the generated `alembic/env.py` with one that pulls the URL from settings and registers our metadata. Key changes from the template:

```python
# alembic/env.py — full file, replaces template
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config import settings
from app.db import Base
import app.models  # noqa: F401 — register models with Base.metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


run_migrations_online()
```

- [ ] **Step 3: Generate the initial migration**

```bash
docker compose exec app alembic revision --autogenerate -m "initial"
```

Verify the file `alembic/versions/0001_initial.py` (or similar) contains `op.create_table("mods", ...)`, `op.create_table("mod_snapshots", ...)`, `op.create_table("admin_users", ...)`.

- [ ] **Step 4: Apply migration**

```bash
docker compose exec app alembic upgrade head
docker compose exec db psql -U modboard -d modboard -c "\dt"
```

Expect to see `admin_users`, `alembic_version`, `mod_snapshots`, `mods`.

- [ ] **Step 5: Commit**

```bash
git add alembic.ini alembic/
git commit -m "feat: alembic async migrations + initial schema"
git push
```

---

### Task 5: Steam API client + tests

**Files:**
- Create: `app/services/__init__.py`, `app/services/steam_api.py`, `tests/conftest.py`, `tests/test_steam_api.py`

- [ ] **Step 1: Write the client**

`app/services/__init__.py`: empty.

`app/services/steam_api.py`:

```python
from typing import TypedDict

import httpx


class FileDetails(TypedDict, total=False):
    publishedfileid: str
    title: str
    description: str
    creator: str
    consumer_app_id: int
    file_size: int
    preview_url: str
    time_created: int
    time_updated: int
    subscriptions: int
    lifetime_subscriptions: int
    favorited: int
    views: int
    visibility: int
    banned: int


GET_DETAILS_URL = (
    "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
)


async def get_published_file_details(file_ids: list[int]) -> list[FileDetails]:
    body: dict[str, str | int] = {"itemcount": len(file_ids)}
    for i, fid in enumerate(file_ids):
        body[f"publishedfileids[{i}]"] = fid
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(GET_DETAILS_URL, data=body)
        r.raise_for_status()
        payload = r.json()
    return payload["response"]["publishedfiledetails"]
```

- [ ] **Step 2: Write the test**

`tests/conftest.py`:

```python
import pytest


@pytest.fixture
def known_mod_ids() -> list[int]:
    return [3721500094, 3721918079, 3724689682]
```

`tests/test_steam_api.py`:

```python
import pytest

from app.services.steam_api import get_published_file_details


@pytest.mark.asyncio
async def test_returns_three_published_pz_mods(known_mod_ids):
    details = await get_published_file_details(known_mod_ids)
    assert len(details) == 3
    titles = {d["title"] for d in details}
    assert "Weapon Enhancement" in titles
    assert "LifeMilestones" in titles
    assert "DayCount" in titles
    # PZ app id sanity
    for d in details:
        assert d["consumer_app_id"] == 108600
```

- [ ] **Step 3: Run tests**

```bash
docker compose exec app pytest -v tests/test_steam_api.py
```

Expect PASS.

- [ ] **Step 4: Commit**

```bash
git add app/services/ tests/
git commit -m "feat: Steam GetPublishedFileDetails async client + live test"
git push
```

---

### Task 6: Workshop HTML scraper + comment count

**Files:**
- Create: `app/services/workshop_scrape.py`, `tests/test_workshop_scrape.py`

- [ ] **Step 1: Write the scraper**

`app/services/workshop_scrape.py`:

```python
import re

import httpx
from bs4 import BeautifulSoup


PAGE_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}"
COMMENT_URL = (
    "https://steamcommunity.com/comment/PublishedFile_Public/render/"
    "{creator_id}/{mod_id}/"
)
UA = "Mozilla/5.0 (compatible; ModBoard/0.1)"


def _num(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


async def scrape_display_labels(mod_id: int) -> dict[str, int | None]:
    """Return Unique Visitors / Current Subscribers / Current Favorites
    as the user sees them on the Workshop page."""
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": UA}) as client:
        r = await client.get(PAGE_URL.format(mod_id=mod_id))
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    out: dict[str, int | None] = {"visitors": None, "subscribers": None, "favorites": None}
    for stat in soup.select(".stats_table tr"):
        cells = stat.find_all("td")
        if len(cells) != 2:
            continue
        value, label = cells[0].get_text(strip=True), cells[1].get_text(strip=True)
        n = _num(value)
        lab = label.lower()
        if "visitor" in lab:
            out["visitors"] = n
        elif "subscriber" in lab:
            out["subscribers"] = n
        elif "favorite" in lab:
            out["favorites"] = n
    return out


async def fetch_comment_total(mod_id: int, creator_id: str) -> int | None:
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": UA}) as client:
        r = await client.post(
            COMMENT_URL.format(creator_id=creator_id, mod_id=mod_id),
            data={"count": 1, "start": 0, "oldestfirst": 1},
        )
        r.raise_for_status()
        data = r.json()
    return int(data.get("total_count")) if data.get("success") else None
```

- [ ] **Step 2: Test it live**

`tests/test_workshop_scrape.py`:

```python
import pytest

from app.services.workshop_scrape import fetch_comment_total, scrape_display_labels


@pytest.mark.asyncio
async def test_daycount_has_labels():
    out = await scrape_display_labels(3724689682)
    # All three numbers exist (>= 0 in case PZ ever zeroes them)
    assert out["visitors"] is not None
    assert out["subscribers"] is not None
    assert out["favorites"] is not None


@pytest.mark.asyncio
async def test_lifemilestones_has_comments():
    total = await fetch_comment_total(3721918079, "76561198279237042")
    assert total is not None
    assert total >= 4   # was 4 at brainstorm time, may grow
```

```bash
docker compose exec app pytest -v tests/test_workshop_scrape.py
```

- [ ] **Step 3: Commit**

```bash
git add app/services/workshop_scrape.py tests/test_workshop_scrape.py
git commit -m "feat: workshop HTML scraper + comment total endpoint"
git push
```

---

### Task 7: Background poller

**Files:**
- Create: `app/services/poller.py`
- Modify: `app/main.py`

- [ ] **Step 1: Write the poller**

`app/services/poller.py`:

```python
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Mod, ModSnapshot
from app.services.steam_api import get_published_file_details
from app.services.workshop_scrape import fetch_comment_total, scrape_display_labels

log = logging.getLogger(__name__)


async def poll_once() -> None:
    async with SessionLocal() as session:
        mods = (await session.execute(select(Mod))).scalars().all()
        if not mods:
            log.info("no mods to poll")
            return
        details = await get_published_file_details([m.id for m in mods])
        by_id = {int(d["publishedfileid"]): d for d in details}
        for mod in mods:
            api = by_id.get(mod.id) or {}
            try:
                labels = await scrape_display_labels(mod.id)
            except Exception as e:
                log.warning("scrape failed for %s: %s", mod.id, e)
                labels = {"visitors": None, "subscribers": None, "favorites": None}
            try:
                comments = await fetch_comment_total(mod.id, settings.steam_creator_id)
            except Exception as e:
                log.warning("comment fetch failed for %s: %s", mod.id, e)
                comments = None
            snap = ModSnapshot(
                mod_id=mod.id,
                captured_at=datetime.now(timezone.utc),
                subscriptions=api.get("subscriptions"),
                lifetime_subs=api.get("lifetime_subscriptions"),
                favorited=api.get("favorited"),
                views=api.get("views"),
                comments_count=comments,
                last_updated=(
                    datetime.fromtimestamp(api["time_updated"], tz=timezone.utc)
                    if api.get("time_updated") else None
                ),
                visitors_display=labels["visitors"],
                subscribers_display=labels["subscribers"],
                favorites_display=labels["favorites"],
            )
            session.add(snap)
            # also sync title/description on the mod row
            if api.get("title") and not mod.title:
                mod.title = api["title"]
            if api.get("preview_url") and not mod.thumbnail_url:
                mod.thumbnail_url = api["preview_url"]
        await session.commit()
        log.info("polled %d mods", len(mods))


async def poller_task() -> None:
    interval = settings.poll_interval_minutes * 60
    while True:
        try:
            await poll_once()
        except Exception:
            log.exception("poll_once crashed; will retry next interval")
        await asyncio.sleep(interval)
```

- [ ] **Step 2: Launch on startup**

Replace `app/main.py`:

```python
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.services.poller import poller_task

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poller_task())
    yield
    task.cancel()


app = FastAPI(title="ModBoard", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 3: Verify**

```bash
docker compose restart app
docker compose logs -f app
```

With no mods seeded yet, the log should show `no mods to poll` every interval (no errors). Stop following with Ctrl-C.

- [ ] **Step 4: Commit**

```bash
git add app/services/poller.py app/main.py
git commit -m "feat: background poller as asyncio lifespan task"
git push
```

---

### Task 8: Public mod list page

**Files:**
- Create: `app/templates/base.html`, `app/templates/mod_list.html`, `app/static/style.css`, `app/routes/__init__.py`, `app/routes/public.py`
- Modify: `app/main.py`

- [ ] **Step 1: Base template**

`app/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}ModBoard{% endblock %}</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header><a href="/">ModBoard</a></header>
  <main>{% block body %}{% endblock %}</main>
</body>
</html>
```

- [ ] **Step 2: Mod list template**

`app/templates/mod_list.html`:

```html
{% extends "base.html" %}
{% block body %}
<h1>Mods</h1>
<ul class="modlist">
{% for row in mods %}
  <li>
    <a href="/mod/{{ row.mod.id }}">
      {% if row.mod.thumbnail_url %}<img src="{{ row.mod.thumbnail_url }}" alt="">{% endif %}
      <strong>{{ row.mod.title or row.mod.name }}</strong>
    </a>
    {% if row.snap %}
      <span>Subscribers: {{ row.snap.subscribers_display or 0 }}</span>
      <span>Favorites: {{ row.snap.favorites_display or 0 }}</span>
      <span>Comments: {{ row.snap.comments_count or 0 }}</span>
    {% endif %}
  </li>
{% endfor %}
</ul>
{% endblock %}
```

- [ ] **Step 3: Minimal CSS**

`app/static/style.css`:

```css
body { font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; }
header a { font-size: 1.25rem; font-weight: 700; text-decoration: none; color: inherit; }
.modlist { list-style: none; padding: 0; }
.modlist li { display: flex; gap: 1rem; align-items: center; padding: 0.75rem 0; border-bottom: 1px solid #eee; }
.modlist img { width: 64px; height: 64px; object-fit: cover; }
.modlist span { margin-left: auto; font-size: 0.875rem; color: #666; }
```

- [ ] **Step 4: Public routes**

`app/routes/__init__.py`: empty.

`app/routes/public.py`:

```python
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Mod, ModSnapshot

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


async def _latest_snapshot(session: AsyncSession, mod_id: int) -> ModSnapshot | None:
    q = (
        select(ModSnapshot)
        .where(ModSnapshot.mod_id == mod_id)
        .order_by(ModSnapshot.captured_at.desc())
        .limit(1)
    )
    return (await session.execute(q)).scalar_one_or_none()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, session: AsyncSession = Depends(get_db)):
    mods = (
        await session.execute(select(Mod).where(Mod.public.is_(True)).order_by(Mod.name))
    ).scalars().all()
    rows = [{"mod": m, "snap": await _latest_snapshot(session, m.id)} for m in mods]
    return templates.TemplateResponse(request, "mod_list.html", {"mods": rows})
```

- [ ] **Step 5: Wire static + routes in app**

In `app/main.py` add (above the existing routes):

```python
from fastapi.staticfiles import StaticFiles

from app.routes import public as public_routes

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(public_routes.router)
```

- [ ] **Step 6: Smoke**

```bash
docker compose restart app
# open http://localhost:8000/  -> empty list (no mods yet, no error)
```

- [ ] **Step 7: Commit**

```bash
git add app/templates/ app/static/ app/routes/
git commit -m "feat: public mod list with Jinja + minimal CSS"
git push
```

---

### Task 9: Admin auth + mod CRUD

**Files:**
- Create: `app/services/auth.py`, `app/routes/admin.py`, `app/templates/admin_mods.html`
- Modify: `app/main.py`

- [ ] **Step 1: HTTP Basic admin dependency**

`app/services/auth.py`:

```python
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

security = HTTPBasic()


def require_admin(creds: HTTPBasicCredentials = Depends(security)) -> str:
    ok_user = secrets.compare_digest(creds.username, settings.admin_username)
    ok_pass = secrets.compare_digest(creds.password, settings.admin_password)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return creds.username
```

- [ ] **Step 2: Admin routes (add/list/delete mod)**

`app/routes/admin.py`:

```python
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Mod
from app.services.auth import require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="app/templates")


@router.get("/mods", response_class=HTMLResponse)
async def list_mods(request: Request, session: AsyncSession = Depends(get_db)):
    mods = (await session.execute(select(Mod).order_by(Mod.name))).scalars().all()
    return templates.TemplateResponse(request, "admin_mods.html", {"mods": mods})


@router.post("/mods")
async def add_mod(
    workshop_id: int = Form(...),
    name: str = Form(...),
    session: AsyncSession = Depends(get_db),
):
    mod = Mod(
        id=workshop_id,
        name=name,
        workshop_url=f"https://steamcommunity.com/sharedfiles/filedetails/?id={workshop_id}",
        created_at=datetime.now(timezone.utc),
    )
    session.add(mod)
    await session.commit()
    return RedirectResponse("/admin/mods", status_code=303)


@router.post("/mods/{mod_id}/delete")
async def delete_mod(mod_id: int, session: AsyncSession = Depends(get_db)):
    mod = await session.get(Mod, mod_id)
    if mod is not None:
        await session.delete(mod)
        await session.commit()
    return RedirectResponse("/admin/mods", status_code=303)
```

- [ ] **Step 3: Admin template**

`app/templates/admin_mods.html`:

```html
{% extends "base.html" %}
{% block title %}Admin — Mods{% endblock %}
{% block body %}
<h1>Admin · Mods</h1>

<form method="post" action="/admin/mods">
  <input type="number" name="workshop_id" placeholder="Workshop ID" required>
  <input type="text" name="name" placeholder="Short name (e.g. HoldoutSurvivors)" required>
  <button type="submit">Add</button>
</form>

<table>
  <thead><tr><th>ID</th><th>Name</th><th>Title</th><th></th></tr></thead>
  <tbody>
  {% for m in mods %}
    <tr>
      <td>{{ m.id }}</td>
      <td>{{ m.name }}</td>
      <td>{{ m.title or "—" }}</td>
      <td>
        <form method="post" action="/admin/mods/{{ m.id }}/delete">
          <button type="submit">Delete</button>
        </form>
      </td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 4: Wire admin router**

In `app/main.py` add:

```python
from app.routes import admin as admin_routes

app.include_router(admin_routes.router)
```

- [ ] **Step 5: Test**

```bash
docker compose restart app
# open http://localhost:8000/admin/mods, enter creds from .env
# add three real mods:
#   3721500094  WeaponEnhancement
#   3721918079  LifeMilestones
#   3724689682  DayCount
# they should appear in the list, then on http://localhost:8000/ too (titles empty until next poll)
```

- [ ] **Step 6: Force a manual poll to populate titles**

```bash
docker compose exec app python -c "import asyncio; from app.services.poller import poll_once; asyncio.run(poll_once())"
```

Refresh `/` — titles, thumbnail, and stat numbers should appear.

- [ ] **Step 7: Commit**

```bash
git add app/services/auth.py app/routes/admin.py app/templates/admin_mods.html app/main.py
git commit -m "feat: admin basic auth + mod add/list/delete CRUD"
git push
```

---

### Task 10: Mod detail page

**Files:**
- Create: `app/templates/mod_detail.html`
- Modify: `app/routes/public.py`

- [ ] **Step 1: Add detail route**

In `app/routes/public.py` append:

```python
from fastapi import HTTPException


@router.get("/mod/{mod_id}", response_class=HTMLResponse)
async def mod_detail(
    request: Request, mod_id: int, session: AsyncSession = Depends(get_db)
):
    mod = await session.get(Mod, mod_id)
    if mod is None or not mod.public:
        raise HTTPException(404)
    snap = await _latest_snapshot(session, mod_id)
    return templates.TemplateResponse(
        request, "mod_detail.html", {"mod": mod, "snap": snap}
    )
```

- [ ] **Step 2: Detail template**

`app/templates/mod_detail.html`:

```html
{% extends "base.html" %}
{% block title %}{{ mod.title or mod.name }} — ModBoard{% endblock %}
{% block body %}
<article>
  {% if mod.thumbnail_url %}<img src="{{ mod.thumbnail_url }}" alt="" style="max-width:320px">{% endif %}
  <h1>{{ mod.title or mod.name }}</h1>
  <p><a href="{{ mod.workshop_url }}" target="_blank" rel="noopener">View on Steam Workshop</a></p>
  {% if snap %}
    <ul>
      <li>Unique Visitors: {{ snap.visitors_display or 0 }}</li>
      <li>Current Subscribers: {{ snap.subscribers_display or 0 }}</li>
      <li>Current Favorites: {{ snap.favorites_display or 0 }}</li>
      <li>Comments: {{ snap.comments_count or 0 }}</li>
      <li>Last updated on Steam:
        {{ snap.last_updated.strftime("%Y-%m-%d") if snap.last_updated else "—" }}
      </li>
    </ul>
  {% endif %}
  <p><a href="/mod/{{ mod.id }}/stats">Growth chart →</a></p>
</article>
{% endblock %}
```

- [ ] **Step 3: Smoke**

Open `http://localhost:8000/mod/3724689682` → expect DayCount page with stats.

- [ ] **Step 4: Commit**

```bash
git add app/templates/mod_detail.html app/routes/public.py
git commit -m "feat: per-mod detail page"
git push
```

---

### Task 11: Subscriber growth chart

**Files:**
- Create: `app/templates/mod_stats.html`
- Modify: `app/routes/public.py`

- [ ] **Step 1: Add stats route returning chart data**

In `app/routes/public.py` append:

```python
from datetime import timedelta


@router.get("/mod/{mod_id}/stats", response_class=HTMLResponse)
async def mod_stats(
    request: Request, mod_id: int, session: AsyncSession = Depends(get_db)
):
    mod = await session.get(Mod, mod_id)
    if mod is None or not mod.public:
        raise HTTPException(404)
    q = (
        select(ModSnapshot)
        .where(ModSnapshot.mod_id == mod_id)
        .order_by(ModSnapshot.captured_at.asc())
    )
    snaps = (await session.execute(q)).scalars().all()
    labels = [s.captured_at.strftime("%Y-%m-%d %H:%M") for s in snaps]
    subs   = [s.subscribers_display for s in snaps]
    return templates.TemplateResponse(
        request, "mod_stats.html",
        {"mod": mod, "labels": labels, "subs": subs}
    )
```

- [ ] **Step 2: Chart template**

`app/templates/mod_stats.html`:

```html
{% extends "base.html" %}
{% block title %}{{ mod.title or mod.name }} Stats — ModBoard{% endblock %}
{% block body %}
<h1>{{ mod.title or mod.name }} · Subscriber growth</h1>
<p><a href="/mod/{{ mod.id }}">← back to mod</a></p>
<canvas id="chart" width="900" height="320"></canvas>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script>
new Chart(document.getElementById("chart"), {
  type: "line",
  data: {
    labels: {{ labels|tojson }},
    datasets: [{
      label: "Current Subscribers",
      data: {{ subs|tojson }},
      tension: 0.2,
      fill: false,
    }]
  },
  options: { scales: { x: { ticks: { autoSkip: true, maxTicksLimit: 10 } } } }
});
</script>
{% endblock %}
```

- [ ] **Step 3: Force a couple more polls to get a non-flat line**

```bash
docker compose exec app python -c "import asyncio; from app.services.poller import poll_once; asyncio.run(poll_once())"
# wait a bit, then run again
docker compose exec app python -c "import asyncio; from app.services.poller import poll_once; asyncio.run(poll_once())"
```

Open `http://localhost:8000/mod/3721500094/stats` → expect a chart with 2-3 points.

- [ ] **Step 4: Commit**

```bash
git add app/templates/mod_stats.html app/routes/public.py
git commit -m "feat: per-mod subscriber growth chart via Chart.js"
git push
```

---

### Task 12: End-to-end verification + production deploy

- [ ] **Step 1: Tear down dev, bring up prod compose without override**

```bash
docker compose down
docker compose up -d --build         # uses base file only; cloudflared runs
docker compose logs cloudflared      # confirm tunnel registered
```

- [ ] **Step 2: Verify Cloudflare Tunnel route**

In Cloudflare Zero Trust dashboard:
- Tunnel `modboard` (or your name) → public hostname → `modboard.yourdomain.tld` → `http://app:8000`

Hit `https://modboard.yourdomain.tld/health` → `{"status":"ok"}`.
Hit `https://modboard.yourdomain.tld/` → mod list with the 3 real mods.

- [ ] **Step 3: Manual probe scenarios**

- Visit `/` as anonymous → list is visible
- Visit `/admin/mods` → browser prompts for credentials, accepts the .env values
- Use wrong creds → expect 401
- Visit `/mod/3721500094/stats` → chart renders

- [ ] **Step 4: Confirm backup cron fires**

```bash
docker compose exec db-backup ls -la /backups
```

After one day of uptime should show a `modboard_YYYY-MM-DD.dump` file.

- [ ] **Step 5: README + tag release**

Write `README.md` covering setup, env vars, and the 4 commands needed to deploy (`git clone`, `cp .env.example .env`, `docker compose up -d --build`, `docker compose exec app alembic upgrade head`).

```bash
git add README.md
git commit -m "docs: README with deploy steps"
git tag v0.1.0
git push --tags
```

---

## Self-Review

**Spec coverage** (Phase 1 in spec lists: FastAPI skeleton + SQLite + Jinja templates, mod CRUD, Steam API poller, public mod list + detail, subscriber chart):

- FastAPI skeleton → Task 1 ✓
- Postgres in place of SQLite (per later spec decision) → Tasks 2–4 ✓
- Mod CRUD admin → Task 9 ✓
- Steam API poller → Tasks 5, 7 ✓
- Public mod list → Task 8 ✓
- Mod detail page → Task 10 ✓
- Subscriber chart → Task 11 ✓
- Deployment via Docker + Cloudflare Tunnel → Tasks 2, 12 ✓
- GitHub repo (user added requirement) → Task 1 ✓

**Placeholder scan:** no "TBD", no "implement later", no unspecified categories. Each step has exact paths, exact code, and exact verification commands.

**Type consistency:** ORM names match across tasks (`Mod`, `ModSnapshot`, `AdminUser`). Field names in templates (`snap.subscribers_display`, `snap.comments_count`, etc.) match the model definitions in Task 3.

**Out of Phase 1 (deferred to later phases):**
- Comment scraping + admin inbox + reply composer (Phase 2)
- Changelog/todo CMS (Phase 3)
- Mobile-responsive layout / multi-mod dashboard charts (Phase 4)

---

## Execution Handoff

Plan complete and saved.

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Use `superpowers:subagent-driven-development`.
2. **Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

**Which approach?**
