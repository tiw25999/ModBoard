"""Admin routes — gated by signed mb_admin cookie via main.py middleware."""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models import (
    ForumPost,
    ForumThread,
    Mod,
    ModChangelog,
    ModComment,
    ModDiscussion,
    ModSnapshot,
)
from app.services.poller import poll_once

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


@router.post("/poll-now")
async def trigger_poll():
    asyncio.create_task(poll_once())
    return RedirectResponse("/admin?polled=1", status_code=303)


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
