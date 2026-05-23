"""Bearer-token API for admin scripted writes.

Mounted at /api/admin/* and gated by AdminApiKey instead of the
session cookie. Use this when you want to push data from a script
(curl / n8n / GitHub Action) without going through the deploy +
manual-admin-page workflow.

Key management is at /admin/api-keys (UI, cookie-gated).
Endpoint reference at /admin/api-docs (also cookie-gated).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import (
    NEWS_KINDS,
    ROADMAP_STATUSES,
    AdminApiKey,
    Mod,
    NewsPost,
    RoadmapItem,
)
from app.services.api_key import require_api_key
from app.services.poller import poll_once
from app.services.textfmt import render

router = APIRouter(prefix="/api/admin", tags=["admin-api"])


# Cap for /mods/bulk so a typo'd loop can't insert tens of thousands.
MAX_BULK_MODS = 200

# Shared lock to keep /poll from spawning concurrent scrapes.
_POLL_LOCK = asyncio.Lock()


# ---------- meta -------------------------------------------------------

@router.get("/whoami")
async def whoami(
    key: AdminApiKey = Depends(require_api_key),
) -> dict[str, Any]:
    """Verify a key works. Returns its metadata."""
    return {
        "label": key.label,
        "prefix": key.key_prefix,
        "created_at": key.created_at.isoformat(),
        "expires_at": key.expires_at.isoformat(),
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
    }


# ---------- mods -------------------------------------------------------

def _mod_dict(m: Mod) -> dict[str, Any]:
    return {
        "id": m.id,
        "name": m.name,
        "title": m.title,
        "workshop_url": m.workshop_url,
        "github_url": m.github_url,
        "thumbnail_url": m.thumbnail_url,
        "public": m.public,
        "app_id": m.app_id,
        "app_name": m.app_name,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("/mods")
async def list_mods(
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rows = (await session.execute(select(Mod).order_by(Mod.id))).scalars().all()
    return {"count": len(rows), "mods": [_mod_dict(m) for m in rows]}


@router.get("/mods/{mod_id}")
async def get_mod(
    mod_id: int,
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    m = await session.get(Mod, mod_id)
    if m is None:
        raise HTTPException(404, detail=f"mod {mod_id} not found")
    return _mod_dict(m)


@router.post("/mods", status_code=201)
async def create_mod(
    workshop_id: Annotated[int, Body(..., embed=True)],
    name: Annotated[str, Body(..., embed=True, min_length=1, max_length=64)],
    public: Annotated[bool, Body(embed=True)] = True,
    workshop_url: Annotated[str | None, Body(embed=True)] = None,
    github_url: Annotated[str | None, Body(embed=True)] = None,
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if await session.get(Mod, workshop_id):
        raise HTTPException(409, detail=f"mod {workshop_id} already tracked")
    mod = Mod(
        id=workshop_id,
        name=name.strip()[:64],
        workshop_url=workshop_url or f"https://steamcommunity.com/sharedfiles/filedetails/?id={workshop_id}",
        github_url=github_url,
        public=public,
        created_at=datetime.now(timezone.utc),
    )
    session.add(mod)
    await session.commit()
    return _mod_dict(mod)


@router.patch("/mods/{mod_id}")
async def update_mod(
    mod_id: int,
    body: Annotated[dict[str, Any], Body(...)],
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Partial update. Allowed fields: name, title, workshop_url,
    github_url, thumbnail_url, public."""
    mod = await session.get(Mod, mod_id)
    if mod is None:
        raise HTTPException(404, detail=f"mod {mod_id} not found")
    allowed = {"name", "title", "workshop_url", "github_url", "thumbnail_url", "public"}
    extra = set(body) - allowed
    if extra:
        raise HTTPException(400, detail=f"unknown fields: {sorted(extra)}")
    for k, v in body.items():
        setattr(mod, k, v)
    await session.commit()
    return _mod_dict(mod)


@router.delete("/mods/{mod_id}", status_code=204)
async def delete_mod(
    mod_id: int,
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
):
    mod = await session.get(Mod, mod_id)
    if mod is None:
        raise HTTPException(404, detail=f"mod {mod_id} not found")
    await session.delete(mod)
    await session.commit()
    return None


@router.post("/mods/bulk", status_code=201)
async def bulk_create_mods(
    items: Annotated[list[dict[str, Any]], Body(..., embed=True)],
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Insert multiple mods in one call. Skips any whose workshop_id
    already exists; returns the per-item outcome."""
    if not items:
        raise HTTPException(400, detail="items[] is empty")
    if len(items) > MAX_BULK_MODS:
        raise HTTPException(400, detail=f"max {MAX_BULK_MODS} per call")
    out: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for it in items:
        wid = it.get("workshop_id")
        nm = (it.get("name") or "").strip()[:64]
        if not isinstance(wid, int) or not nm:
            out.append({"workshop_id": wid, "status": "bad_input"})
            continue
        if await session.get(Mod, wid):
            out.append({"workshop_id": wid, "status": "exists"})
            continue
        session.add(Mod(
            id=wid, name=nm,
            workshop_url=it.get("workshop_url") or f"https://steamcommunity.com/sharedfiles/filedetails/?id={wid}",
            github_url=it.get("github_url"),
            public=bool(it.get("public", True)),
            created_at=now,
        ))
        out.append({"workshop_id": wid, "status": "created"})
    await session.commit()
    return {"results": out}


# ---------- poll -------------------------------------------------------

async def _poll_locked() -> None:
    if _POLL_LOCK.locked():
        return
    async with _POLL_LOCK:
        await poll_once()


@router.post("/poll", status_code=202)
async def trigger_poll(
    key: AdminApiKey = Depends(require_api_key),
) -> dict[str, str]:
    """Kick off a Steam scrape cycle. Returns immediately; the poll
    runs in the background. No-op if a poll is already in progress."""
    if _POLL_LOCK.locked():
        return {"status": "already_running"}
    asyncio.create_task(_poll_locked())
    return {"status": "queued"}


# ---------- news -------------------------------------------------------

def _news_dict(n: NewsPost) -> dict[str, Any]:
    return {
        "id": n.id,
        "title": n.title,
        "body": n.body_raw,
        "kind": n.kind,
        "active": n.active,
        "show_banner": n.show_banner,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("/news")
async def list_news(
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rows = (await session.execute(
        select(NewsPost).order_by(desc(NewsPost.created_at)).limit(100)
    )).scalars().all()
    return {"count": len(rows), "news": [_news_dict(n) for n in rows]}


@router.post("/news", status_code=201)
async def create_news(
    title: Annotated[str, Body(..., embed=True, min_length=1, max_length=200)],
    body: Annotated[str, Body(..., embed=True, min_length=1, max_length=10000)],
    kind: Annotated[str, Body(embed=True)] = "info",
    active: Annotated[bool, Body(embed=True)] = True,
    show_banner: Annotated[bool, Body(embed=True)] = False,
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if kind not in NEWS_KINDS:
        raise HTTPException(400, detail=f"kind must be one of {list(NEWS_KINDS)}")
    now = datetime.now(timezone.utc)
    post = NewsPost(
        title=title.strip(), body_raw=body, body_html=render(body),
        kind=kind, active=active, show_banner=show_banner,
        created_at=now, updated_at=now,
    )
    session.add(post)
    await session.commit()
    return _news_dict(post)


@router.patch("/news/{news_id}")
async def update_news(
    news_id: int,
    body: Annotated[dict[str, Any], Body(...)],
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    n = await session.get(NewsPost, news_id)
    if n is None:
        raise HTTPException(404, detail=f"news {news_id} not found")
    allowed = {"title", "body", "kind", "active", "show_banner"}
    extra = set(body) - allowed
    if extra:
        raise HTTPException(400, detail=f"unknown fields: {sorted(extra)}")
    if "kind" in body and body["kind"] not in NEWS_KINDS:
        raise HTTPException(400, detail=f"kind must be one of {list(NEWS_KINDS)}")
    if "body" in body:
        n.body_raw = body["body"]
        n.body_html = render(body["body"])
    for k in ("title", "kind", "active", "show_banner"):
        if k in body:
            setattr(n, k, body[k])
    n.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return _news_dict(n)


@router.delete("/news/{news_id}", status_code=204)
async def delete_news(
    news_id: int,
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
):
    n = await session.get(NewsPost, news_id)
    if n is None:
        raise HTTPException(404, detail=f"news {news_id} not found")
    await session.delete(n)
    await session.commit()
    return None


# ---------- roadmap ----------------------------------------------------

def _road_dict(r: RoadmapItem) -> dict[str, Any]:
    return {
        "id": r.id,
        "mod_id": r.mod_id,
        "title": r.title,
        "body": r.body,
        "status": r.status,
        "position": r.position,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/roadmap/{mod_id}")
async def list_roadmap(
    mod_id: int,
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rows = (await session.execute(
        select(RoadmapItem)
        .where(RoadmapItem.mod_id == mod_id)
        .order_by(RoadmapItem.position, RoadmapItem.id)
    )).scalars().all()
    return {"count": len(rows), "items": [_road_dict(r) for r in rows]}


@router.post("/roadmap", status_code=201)
async def create_roadmap(
    mod_id: Annotated[int, Body(..., embed=True)],
    title: Annotated[str, Body(..., embed=True, min_length=1, max_length=200)],
    body: Annotated[str | None, Body(embed=True)] = None,
    status: Annotated[str, Body(embed=True)] = "planned",
    position: Annotated[int, Body(embed=True)] = 0,
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if status not in ROADMAP_STATUSES:
        raise HTTPException(400, detail=f"status must be one of {list(ROADMAP_STATUSES)}")
    if await session.get(Mod, mod_id) is None:
        raise HTTPException(404, detail=f"mod {mod_id} not found")
    now = datetime.now(timezone.utc)
    item = RoadmapItem(
        mod_id=mod_id, title=title.strip(), body=body, status=status,
        position=position, created_at=now, updated_at=now,
    )
    session.add(item)
    await session.commit()
    return _road_dict(item)


@router.patch("/roadmap/{item_id}")
async def update_roadmap(
    item_id: int,
    body: Annotated[dict[str, Any], Body(...)],
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    item = await session.get(RoadmapItem, item_id)
    if item is None:
        raise HTTPException(404, detail=f"roadmap item {item_id} not found")
    allowed = {"title", "body", "status", "position"}
    extra = set(body) - allowed
    if extra:
        raise HTTPException(400, detail=f"unknown fields: {sorted(extra)}")
    if "status" in body and body["status"] not in ROADMAP_STATUSES:
        raise HTTPException(400, detail=f"status must be one of {list(ROADMAP_STATUSES)}")
    for k, v in body.items():
        setattr(item, k, v)
    item.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return _road_dict(item)


@router.delete("/roadmap/{item_id}", status_code=204)
async def delete_roadmap(
    item_id: int,
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
):
    item = await session.get(RoadmapItem, item_id)
    if item is None:
        raise HTTPException(404, detail=f"roadmap item {item_id} not found")
    await session.delete(item)
    await session.commit()
    return None
