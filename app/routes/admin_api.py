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
import mimetypes
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Form, HTTPException, UploadFile
from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import (
    NEWS_KINDS,
    ROADMAP_STATUSES,
    AdminApiKey,
    Mod,
    ModFile,
    NewsPost,
    RoadmapItem,
)
from app.services import mod_files as mf
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
        "source": m.source,
        "game_name": m.game_name,
        "workshop_url": m.workshop_url,
        "github_url": m.github_url,
        "thumbnail_url": m.thumbnail_url,
        "public": m.public,
        "app_id": m.app_id,
        "app_name": m.app_name,
        "view_count": m.view_count,
        "download_count": m.download_count,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _file_dict(f: ModFile) -> dict[str, Any]:
    return {
        "id": f.id,
        "mod_id": f.mod_id,
        "version": f.version,
        "filename": f.filename,
        "size_bytes": f.size_bytes,
        "content_type": f.content_type,
        "sha256": f.sha256,
        "changelog": f.changelog,
        "download_count": f.download_count,
        "is_current": f.is_current,
        "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
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
    """Partial update. Allowed fields: name, title, description,
    game_name, workshop_url, github_url, thumbnail_url, public."""
    mod = await session.get(Mod, mod_id)
    if mod is None:
        raise HTTPException(404, detail=f"mod {mod_id} not found")
    allowed = {"name", "title", "description", "game_name",
               "workshop_url", "github_url", "thumbnail_url", "public"}
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


@router.post("/mods/manual", status_code=201)
async def create_manual_mod(
    name: Annotated[str, Body(..., embed=True, min_length=1, max_length=64)],
    title: Annotated[str | None, Body(embed=True)] = None,
    description: Annotated[str | None, Body(embed=True)] = None,
    game_name: Annotated[str | None, Body(embed=True)] = None,
    github_url: Annotated[str | None, Body(embed=True)] = None,
    public: Annotated[bool, Body(embed=True)] = True,
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a self-hosted, non-Steam mod. Id comes from
    manual_mod_id_seq (small integers; never collide with Steam ids).
    Upload files afterwards with POST /api/admin/mods/{id}/files."""
    new_id = int((await session.execute(
        text("SELECT nextval('manual_mod_id_seq')")
    )).scalar())
    mod = Mod(
        id=new_id,
        name=name.strip()[:64],
        title=(title.strip()[:256] if title else None) or None,
        description=(description.strip() if description else None) or None,
        game_name=(game_name.strip()[:256] if game_name else None) or None,
        github_url=github_url,
        source="manual",
        public=public,
        workshop_url=None,
        created_at=datetime.now(timezone.utc),
    )
    session.add(mod)
    await session.commit()
    return _mod_dict(mod)


@router.get("/mods/{mod_id}/files")
async def list_mod_files(
    mod_id: int,
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List all uploaded file versions for a mod, newest first."""
    if await session.get(Mod, mod_id) is None:
        raise HTTPException(404, detail=f"mod {mod_id} not found")
    rows = (await session.execute(
        select(ModFile).where(ModFile.mod_id == mod_id)
        .order_by(ModFile.uploaded_at.desc())
    )).scalars().all()
    return {"count": len(rows), "files": [_file_dict(f) for f in rows]}


@router.post("/mods/{mod_id}/files", status_code=201)
async def api_upload_file(
    mod_id: int,
    version: str = Form(...),
    upload: UploadFile = Form(...),
    changelog: str = Form(""),
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Upload a new file version for a mod via bearer key. The mod must
    already exist — create it with POST /api/admin/mods/manual (or the
    admin UI). The new upload becomes the current download."""
    mod = await session.get(Mod, mod_id)
    if mod is None:
        raise HTTPException(404, detail=f"mod {mod_id} not found")
    version = version.strip()[:64] or "unversioned"
    max_bytes = settings.max_upload_mb * 1024 * 1024
    try:
        stored_path, size, sha = await mf.stream_save(
            mod_id, upload.filename or "file", upload, max_bytes
        )
    except mf.UploadTooLarge:
        raise HTTPException(413, detail=f"file exceeds {settings.max_upload_mb} MB")

    old_current = (await session.execute(
        select(ModFile).where(ModFile.mod_id == mod_id, ModFile.is_current.is_(True))
    )).scalars().all()
    for f in old_current:
        f.is_current = False

    row = ModFile(
        mod_id=mod_id, version=version,
        filename=(upload.filename or "file")[:255],
        stored_path=stored_path, size_bytes=size,
        content_type=(mimetypes.guess_type(upload.filename or "")[0] or "application/octet-stream"),
        sha256=sha, changelog=changelog.strip() or None,
        is_current=True, uploaded_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.commit()
    return _file_dict(row)


@router.post("/mods/files/{file_id}/set-current")
async def api_set_current_file(
    file_id: int,
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Mark a specific version as the current download for its mod."""
    f = await session.get(ModFile, file_id)
    if f is None:
        raise HTTPException(404, detail=f"file {file_id} not found")
    others = (await session.execute(
        select(ModFile).where(ModFile.mod_id == f.mod_id, ModFile.is_current.is_(True))
    )).scalars().all()
    for o in others:
        o.is_current = False
    f.is_current = True
    await session.commit()
    return _file_dict(f)


@router.delete("/mods/files/{file_id}", status_code=204)
async def api_delete_file(
    file_id: int,
    key: AdminApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_db),
):
    """Delete a file version (removes the stored file from disk too)."""
    f = await session.get(ModFile, file_id)
    if f is None:
        raise HTTPException(404, detail=f"file {file_id} not found")
    mf.delete_file(f.stored_path)
    await session.delete(f)
    await session.commit()
    return None


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
