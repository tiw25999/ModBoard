"""Admin routes — gated by signed mb_admin cookie via main.py middleware."""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import get_db
from app.models import (
    NEWS_KINDS,
    ROADMAP_STATUSES,
    AdminApiKey,
    ForumPost,
    ForumThread,
    Mod,
    ModChangelog,
    ModComment,
    ModDiscussion,
    ModFile,
    ModSnapshot,
    NewsPost,
    RoadmapItem,
)
from app.services import mod_files as mf
from app.services.api_key import mint_key
from app.services.audit import log_event
from app.services.poller import poll_once
from app.services.textfmt import render

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")


async def _count(session: AsyncSession, model) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar() or 0)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: AsyncSession = Depends(get_db)):
    totals = {
        "mods": await _count(session, Mod),
        "snapshots": await _count(session, ModSnapshot),
        "comments": await _count(session, ModComment),
        "discussions": await _count(session, ModDiscussion),
        "changelogs": await _count(session, ModChangelog),
        "threads": await _count(session, ForumThread),
        "replies": await _count(session, ForumPost),
    }

    mods = (await session.execute(select(Mod).order_by(Mod.name))).scalars().all()
    per_mod: list[dict] = []
    total_subs = 0
    total_visitors = 0
    last_poll: datetime | None = None
    for m in mods:
        snap = (
            await session.execute(
                select(ModSnapshot)
                .where(ModSnapshot.mod_id == m.id)
                .order_by(ModSnapshot.captured_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        snap_count = int(
            (await session.execute(
                select(func.count()).select_from(ModSnapshot).where(ModSnapshot.mod_id == m.id)
            )).scalar() or 0
        )
        comments_n = int(
            (await session.execute(
                select(func.count()).select_from(ModComment).where(ModComment.mod_id == m.id)
            )).scalar() or 0
        )
        discussions_n = int(
            (await session.execute(
                select(func.count()).select_from(ModDiscussion).where(ModDiscussion.mod_id == m.id)
            )).scalar() or 0
        )
        changelogs_n = int(
            (await session.execute(
                select(func.count()).select_from(ModChangelog).where(ModChangelog.mod_id == m.id)
            )).scalar() or 0
        )
        if snap:
            if snap.subscribers_display:
                total_subs += snap.subscribers_display
            if snap.visitors_display:
                total_visitors += snap.visitors_display
            if last_poll is None or snap.captured_at > last_poll:
                last_poll = snap.captured_at
        per_mod.append({
            "mod": m,
            "snap": snap,
            "snap_count": snap_count,
            "comments": comments_n,
            "discussions": discussions_n,
            "changelogs": changelogs_n,
        })

    recent_comments = (
        await session.execute(
            select(ModComment).options(selectinload(ModComment.mod))
            .order_by(ModComment.posted_at.desc().nulls_last())
            .limit(8)
        )
    ).scalars().all()
    recent_changelogs = (
        await session.execute(
            select(ModChangelog).options(selectinload(ModChangelog.mod))
            .order_by(ModChangelog.posted_at.desc().nulls_last())
            .limit(8)
        )
    ).scalars().all()
    recent_threads = (
        await session.execute(
            select(ForumThread).options(selectinload(ForumThread.mod))
            .order_by(ForumThread.created_at.desc())
            .limit(8)
        )
    ).scalars().all()

    games = sorted({m.app_name for m in mods if m.app_name})

    return templates.TemplateResponse(
        request, "admin_dashboard.html",
        {
            "totals": totals,
            "per_mod": per_mod,
            "total_subs": total_subs,
            "total_visitors": total_visitors,
            "last_poll": last_poll,
            "games": games,
            "recent_comments": recent_comments,
            "recent_changelogs": recent_changelogs,
            "recent_threads": recent_threads,
        },
    )


@router.get("/mods", response_class=HTMLResponse)
async def list_mods(request: Request, session: AsyncSession = Depends(get_db)):
    mods = (await session.execute(select(Mod).order_by(Mod.name))).scalars().all()
    return templates.TemplateResponse(request, "admin_mods.html", {"mods": mods})


# Single shared lock so an admin clicking "Poll now" 10 times doesn't
# spawn 10 concurrent Steam scrapes (which would race on inserts, fire
# duplicate notifications, and risk a Steam IP ban).
_POLL_LOCK = asyncio.Lock()


async def _poll_once_locked() -> None:
    if _POLL_LOCK.locked():
        return
    async with _POLL_LOCK:
        await poll_once()


@router.post("/poll-now")
async def trigger_poll():
    if _POLL_LOCK.locked():
        return RedirectResponse("/admin?polled=busy", status_code=303)
    asyncio.create_task(_poll_once_locked())
    return RedirectResponse("/admin?polled=1", status_code=303)


# ---------- admin API keys -----------------------------------------------

@router.get("/api-keys", response_class=HTMLResponse)
async def api_keys_page(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """List existing API keys + form to mint a new one. The plain
    secret is only shown ONCE, immediately after creation, via the
    `new_key` query param the create endpoint redirects to."""
    keys = (await session.execute(
        select(AdminApiKey).order_by(AdminApiKey.created_at.desc())
    )).scalars().all()
    return templates.TemplateResponse(
        request, "admin_api_keys.html",
        {
            "keys": keys,
            "now": datetime.now(timezone.utc),
            "newly_created": request.query_params.get("new_key", ""),
            "newly_created_label": request.query_params.get("new_label", ""),
        },
    )


@router.post("/api-keys")
async def api_keys_create(
    request: Request,
    label: str = Form(...),
    ttl_hours: int = Form(24),
    session: AsyncSession = Depends(get_db),
):
    label = label.strip()[:64] or "unnamed"
    ttl_hours = max(1, min(720, int(ttl_hours)))  # clamp 1h..30d
    from datetime import timedelta
    raw, h, prefix, expires_at = mint_key(timedelta(hours=ttl_hours))
    now = datetime.now(timezone.utc)
    session.add(AdminApiKey(
        key_hash=h, key_prefix=prefix, label=label,
        created_at=now, expires_at=expires_at,
    ))
    await log_event(session, "admin_action", request,
                    detail=f"create api_key label={label} ttl={ttl_hours}h")
    await session.commit()
    from urllib.parse import urlencode
    qs = urlencode({"new_key": raw, "new_label": label})
    return RedirectResponse(f"/admin/api-keys?{qs}", status_code=303)


@router.post("/api-keys/{key_id}/revoke")
async def api_keys_revoke(
    request: Request,
    key_id: int,
    session: AsyncSession = Depends(get_db),
):
    key = await session.get(AdminApiKey, key_id)
    if key is None:
        return RedirectResponse("/admin/api-keys?error=not_found", status_code=303)
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        await log_event(session, "admin_action", request,
                        detail=f"revoke api_key id={key.id} label={key.label}")
        await session.commit()
    return RedirectResponse("/admin/api-keys?revoked=1", status_code=303)


@router.get("/api-docs", response_class=HTMLResponse)
async def api_docs_page(request: Request):
    """Reference + curl examples for the bearer-token API."""
    return templates.TemplateResponse(request, "admin_api_docs.html", {})


# ---------- ops dashboard --------------------------------------------------

@router.get("/debug", response_class=HTMLResponse)
async def debug_page(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Live numbers that help when investigating "is it slow?" reports:
    DB pool, in-memory cache, table row counts, recent migration. Read-only."""
    from app.db import engine
    from app.services import cache as _cache_mod
    from app.services import ratelimit as _rl_mod
    from sqlalchemy import text as _text

    # SQLAlchemy pool stats — checked-out, overflow, etc.
    pool = engine.pool
    pool_info = {
        "size": getattr(pool, "size", lambda: 0)(),
        "checked_in": getattr(pool, "checkedin", lambda: 0)(),
        "checked_out": getattr(pool, "checkedout", lambda: 0)(),
        "overflow": getattr(pool, "overflow", lambda: 0)(),
    }

    # In-memory cache size
    cache_entries = len(getattr(_cache_mod, "_store", {}))
    rl_entries = len(getattr(_rl_mod, "_buckets", {}))

    # Row counts (cheap on indexed tables; cap with EXPLAIN if needed)
    counts: dict[str, int] = {}
    for tbl in (
        "users", "mods", "mod_snapshots", "mod_comments", "mod_changelogs",
        "mod_discussions", "forum_threads", "forum_posts", "forum_upvotes",
        "forum_reactions", "notifications", "mod_subscriptions",
        "news_posts", "mod_roadmap_items", "security_events", "admin_api_keys",
    ):
        try:
            n = (await session.execute(_text(f"SELECT count(*) FROM {tbl}"))).scalar()
            counts[tbl] = int(n or 0)
        except Exception:
            counts[tbl] = -1

    # Postgres version + active connection count
    pg_version = ""
    pg_conns = 0
    try:
        pg_version = (await session.execute(_text("SHOW server_version"))).scalar() or ""
        pg_conns = int((await session.execute(
            _text("SELECT count(*) FROM pg_stat_activity")
        )).scalar() or 0)
    except Exception:
        pass

    # Current alembic head
    alembic_head = ""
    try:
        alembic_head = (await session.execute(
            _text("SELECT version_num FROM alembic_version")
        )).scalar() or ""
    except Exception:
        pass

    return templates.TemplateResponse(
        request, "admin_debug.html",
        {
            "pool": pool_info,
            "cache_entries": cache_entries,
            "rl_entries": rl_entries,
            "counts": counts,
            "pg_version": pg_version,
            "pg_conns": pg_conns,
            "alembic_head": alembic_head,
        },
    )


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


@router.post("/mods/manual")
async def add_manual_mod(
    request: Request,
    name: str = Form(...),
    game_name: str = Form(""),
    title: str = Form(""),
    description: str = Form(""),
    public: str = Form(""),
    session: AsyncSession = Depends(get_db),
):
    """Create a self-hosted, non-Steam mod. Id comes from
    manual_mod_id_seq (small integers; never collide with Steam ids)."""
    name = name.strip()[:64]
    if not name:
        return RedirectResponse("/admin/mods", status_code=303)
    new_id = int((await session.execute(
        text("SELECT nextval('manual_mod_id_seq')")
    )).scalar())
    mod = Mod(
        id=new_id,
        name=name,
        title=title.strip()[:256] or None,
        description=description.strip() or None,
        game_name=game_name.strip()[:256] or None,
        source="manual",
        public=bool(public),
        workshop_url=None,
        created_at=datetime.now(timezone.utc),
    )
    session.add(mod)
    await log_event(session, "admin_action", request,
                    detail=f"create manual mod id={new_id} name={name}")
    await session.commit()
    return RedirectResponse(f"/admin/mods/{new_id}/files", status_code=303)


@router.post("/mods/{mod_id}/delete")
async def delete_mod(mod_id: int, session: AsyncSession = Depends(get_db)):
    mod = await session.get(Mod, mod_id)
    if mod is not None:
        await session.delete(mod)
        await session.commit()
    return RedirectResponse("/admin/mods", status_code=303)


# ---------- Manual mod file versions --------------------------------------

@router.get("/mods/{mod_id}/files", response_class=HTMLResponse)
async def admin_mod_files(
    request: Request,
    mod_id: int,
    session: AsyncSession = Depends(get_db),
):
    mod = await session.get(Mod, mod_id)
    if mod is None:
        from fastapi import HTTPException
        raise HTTPException(404)
    files = (await session.execute(
        select(ModFile).where(ModFile.mod_id == mod_id)
        .order_by(ModFile.uploaded_at.desc())
    )).scalars().all()
    return templates.TemplateResponse(
        request, "admin_mod_files.html",
        {"mod": mod, "files": files, "max_mb": settings.max_upload_mb},
    )


@router.post("/mods/{mod_id}/files")
async def admin_upload_file(
    request: Request,
    mod_id: int,
    version: str = Form(...),
    changelog: str = Form(""),
    upload: UploadFile = Form(...),
    session: AsyncSession = Depends(get_db),
):
    mod = await session.get(Mod, mod_id)
    if mod is None:
        from fastapi import HTTPException
        raise HTTPException(404)
    version = version.strip()[:64] or "unversioned"
    max_bytes = settings.max_upload_mb * 1024 * 1024
    try:
        stored_path, size, sha = await mf.stream_save(
            mod_id, upload.filename or "file", upload, max_bytes
        )
    except mf.UploadTooLarge:
        return RedirectResponse(f"/admin/mods/{mod_id}/files?error=too_large", status_code=303)

    # New upload becomes the current version; demote the old current.
    old_current = (await session.execute(
        select(ModFile).where(ModFile.mod_id == mod_id, ModFile.is_current.is_(True))
    )).scalars().all()
    for f in old_current:
        f.is_current = False

    session.add(ModFile(
        mod_id=mod_id,
        version=version,
        filename=(upload.filename or "file")[:255],
        stored_path=stored_path,
        size_bytes=size,
        content_type=(upload.content_type or None),
        sha256=sha,
        changelog=changelog.strip() or None,
        is_current=True,
        uploaded_at=datetime.now(timezone.utc),
    ))
    await log_event(session, "admin_action", request,
                    detail=f"upload file mod={mod_id} version={version} size={size}")
    await session.commit()
    return RedirectResponse(f"/admin/mods/{mod_id}/files", status_code=303)


@router.post("/mods/files/{file_id}/set-current")
async def admin_set_current_file(
    file_id: int,
    session: AsyncSession = Depends(get_db),
):
    f = await session.get(ModFile, file_id)
    if f is None:
        from fastapi import HTTPException
        raise HTTPException(404)
    others = (await session.execute(
        select(ModFile).where(ModFile.mod_id == f.mod_id, ModFile.is_current.is_(True))
    )).scalars().all()
    for o in others:
        o.is_current = False
    f.is_current = True
    await session.commit()
    return RedirectResponse(f"/admin/mods/{f.mod_id}/files", status_code=303)


@router.post("/mods/files/{file_id}/delete")
async def admin_delete_file(
    request: Request,
    file_id: int,
    session: AsyncSession = Depends(get_db),
):
    f = await session.get(ModFile, file_id)
    if f is None:
        from fastapi import HTTPException
        raise HTTPException(404)
    mod_id = f.mod_id
    mf.delete_file(f.stored_path)
    await session.delete(f)
    await log_event(session, "admin_action", request,
                    detail=f"delete file id={file_id} mod={mod_id}")
    await session.commit()
    return RedirectResponse(f"/admin/mods/{mod_id}/files", status_code=303)


# ---------- Roadmap management ---------------------------------------------

@router.get("/mods/{mod_id}/roadmap", response_class=HTMLResponse)
async def admin_roadmap(
    request: Request,
    mod_id: int,
    session: AsyncSession = Depends(get_db),
):
    mod = await session.get(Mod, mod_id)
    if mod is None:
        from fastapi import HTTPException
        raise HTTPException(404)
    items = (
        await session.execute(
            select(RoadmapItem)
            .where(RoadmapItem.mod_id == mod_id)
            .order_by(RoadmapItem.position.asc(), RoadmapItem.id.asc())
        )
    ).scalars().all()
    return templates.TemplateResponse(
        request, "admin_roadmap.html",
        {"mod": mod, "items": items, "statuses": ROADMAP_STATUSES},
    )


@router.post("/mods/{mod_id}/roadmap")
async def add_roadmap_item(
    mod_id: int,
    title: str = Form(...),
    body: str = Form(""),
    status: str = Form("planned"),
    session: AsyncSession = Depends(get_db),
):
    title = title.strip()[:200]
    if not title:
        return RedirectResponse(f"/admin/mods/{mod_id}/roadmap", status_code=303)
    status = status if status in ROADMAP_STATUSES else "planned"
    now = datetime.now(timezone.utc)
    # Append at end of list
    max_pos = int(
        (await session.execute(
            select(func.max(RoadmapItem.position)).where(RoadmapItem.mod_id == mod_id)
        )).scalar() or 0
    )
    session.add(RoadmapItem(
        mod_id=mod_id, title=title, body=body.strip() or None,
        status=status, position=max_pos + 1,
        created_at=now, updated_at=now,
    ))
    await session.commit()
    return RedirectResponse(f"/admin/mods/{mod_id}/roadmap", status_code=303)


@router.post("/roadmap/{item_id}/update")
async def update_roadmap_item(
    item_id: int,
    title: str = Form(...),
    body: str = Form(""),
    status: str = Form(...),
    session: AsyncSession = Depends(get_db),
):
    item = await session.get(RoadmapItem, item_id)
    if item is None:
        from fastapi import HTTPException
        raise HTTPException(404)
    item.title = title.strip()[:200] or item.title
    item.body = body.strip() or None
    if status in ROADMAP_STATUSES:
        item.status = status
    item.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return RedirectResponse(f"/admin/mods/{item.mod_id}/roadmap", status_code=303)


@router.post("/roadmap/{item_id}/delete")
async def delete_roadmap_item(
    item_id: int,
    session: AsyncSession = Depends(get_db),
):
    item = await session.get(RoadmapItem, item_id)
    if item is None:
        from fastapi import HTTPException
        raise HTTPException(404)
    mod_id = item.mod_id
    await session.delete(item)
    await session.commit()
    return RedirectResponse(f"/admin/mods/{mod_id}/roadmap", status_code=303)


@router.post("/roadmap/{item_id}/move")
async def move_roadmap_item(
    item_id: int,
    direction: str = Form(...),  # "up" or "down"
    session: AsyncSession = Depends(get_db),
):
    item = await session.get(RoadmapItem, item_id)
    if item is None:
        from fastapi import HTTPException
        raise HTTPException(404)
    # Find neighbor in chosen direction
    if direction == "up":
        neighbor = (await session.execute(
            select(RoadmapItem)
            .where(RoadmapItem.mod_id == item.mod_id, RoadmapItem.position < item.position)
            .order_by(RoadmapItem.position.desc()).limit(1)
        )).scalar_one_or_none()
    else:
        neighbor = (await session.execute(
            select(RoadmapItem)
            .where(RoadmapItem.mod_id == item.mod_id, RoadmapItem.position > item.position)
            .order_by(RoadmapItem.position.asc()).limit(1)
        )).scalar_one_or_none()
    if neighbor is not None:
        item.position, neighbor.position = neighbor.position, item.position
        await session.commit()
    return RedirectResponse(f"/admin/mods/{item.mod_id}/roadmap", status_code=303)


# ---------- News / announcements -------------------------------------------

@router.get("/news", response_class=HTMLResponse)
async def admin_news_list(request: Request, session: AsyncSession = Depends(get_db)):
    posts = (
        await session.execute(
            select(NewsPost).order_by(NewsPost.created_at.desc())
        )
    ).scalars().all()
    return templates.TemplateResponse(
        request, "admin_news.html",
        {"posts": posts, "kinds": NEWS_KINDS},
    )


@router.post("/news")
async def admin_news_create(
    title: str = Form(...),
    body: str = Form(...),
    kind: str = Form("info"),
    show_banner: str = Form(""),
    session: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    title = title.strip()[:200]
    body = body.strip()
    if not title or not body:
        return RedirectResponse("/admin/news", status_code=303)
    session.add(NewsPost(
        title=title,
        body_html=render(body),
        body_raw=body,
        kind=kind if kind in NEWS_KINDS else "info",
        active=True,
        show_banner=bool(show_banner),
        created_at=now,
        updated_at=now,
    ))
    await session.commit()
    return RedirectResponse("/admin/news", status_code=303)


@router.post("/news/{post_id}/update")
async def admin_news_update(
    post_id: int,
    title: str = Form(...),
    body: str = Form(...),
    kind: str = Form("info"),
    active: str = Form(""),
    show_banner: str = Form(""),
    session: AsyncSession = Depends(get_db),
):
    post = await session.get(NewsPost, post_id)
    if post is None:
        from fastapi import HTTPException
        raise HTTPException(404)
    post.title = title.strip()[:200] or post.title
    body = body.strip()
    if body:
        post.body_raw = body
        post.body_html = render(body)
    if kind in NEWS_KINDS:
        post.kind = kind
    post.active = bool(active)
    post.show_banner = bool(show_banner)
    post.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return RedirectResponse("/admin/news", status_code=303)


@router.post("/news/{post_id}/delete")
async def admin_news_delete(
    post_id: int,
    session: AsyncSession = Depends(get_db),
):
    post = await session.get(NewsPost, post_id)
    if post is not None:
        await session.delete(post)
        await session.commit()
    return RedirectResponse("/admin/news", status_code=303)


# ---------- Forum moderation inbox ----------------------------------------

@router.get("/forum", response_class=HTMLResponse)
async def forum_moderation(
    request: Request,
    filter: str = "all",
    session: AsyncSession = Depends(get_db),
):
    """One-page moderation surface: filter chips at top, thread list with
    inline pin/lock/status/delete, plus a side feed of the latest replies
    so the operator can see brand-new noise without opening each thread."""
    q = (
        select(ForumThread)
        .options(selectinload(ForumThread.mod))
        .order_by(ForumThread.last_post_at.desc())
        .limit(100)
    )
    if filter == "locked":
        q = q.where(ForumThread.locked.is_(True))
    elif filter == "wontfix":
        q = q.where(ForumThread.status == "wontfix")
    elif filter == "open":
        q = q.where(ForumThread.status == "open")
    elif filter == "addressed":
        q = q.where(ForumThread.status == "addressed")
    threads = (await session.execute(q)).scalars().all()

    recent_replies = (
        await session.execute(
            select(ForumPost)
            .order_by(ForumPost.created_at.desc())
            .limit(15)
        )
    ).scalars().all()
    # Index replies by thread for the side feed display
    thread_lookup = {t.id: t for t in (await session.execute(
        select(ForumThread).options(selectinload(ForumThread.mod))
        .where(ForumThread.id.in_([p.thread_id for p in recent_replies]) if recent_replies else select(ForumThread.id).where(ForumThread.id == -1))
    )).scalars().all()}

    totals = {
        "all": await _count(session, ForumThread),
        "open": int((await session.execute(
            select(func.count()).select_from(ForumThread).where(ForumThread.status == "open")
        )).scalar() or 0),
        "addressed": int((await session.execute(
            select(func.count()).select_from(ForumThread).where(ForumThread.status == "addressed")
        )).scalar() or 0),
        "wontfix": int((await session.execute(
            select(func.count()).select_from(ForumThread).where(ForumThread.status == "wontfix")
        )).scalar() or 0),
        "locked": int((await session.execute(
            select(func.count()).select_from(ForumThread).where(ForumThread.locked.is_(True))
        )).scalar() or 0),
        "replies": await _count(session, ForumPost),
    }

    return templates.TemplateResponse(
        request, "admin_forum.html",
        {
            "threads": threads,
            "recent_replies": recent_replies,
            "thread_lookup": thread_lookup,
            "totals": totals,
            "active_filter": filter,
        },
    )


@router.post("/forum/post/{post_id}/delete")
async def admin_delete_post(post_id: int, session: AsyncSession = Depends(get_db)):
    post = await session.get(ForumPost, post_id)
    if post is not None:
        thread = await session.get(ForumThread, post.thread_id)
        await session.delete(post)
        if thread is not None:
            thread.reply_count = max(0, thread.reply_count - 1)
        await session.commit()
    return RedirectResponse("/admin/forum", status_code=303)


@router.post("/forum/thread/{thread_id}/delete")
async def admin_delete_thread(thread_id: int, session: AsyncSession = Depends(get_db)):
    thread = await session.get(ForumThread, thread_id)
    if thread is not None:
        await session.delete(thread)
        await session.commit()
    return RedirectResponse("/admin/forum", status_code=303)
