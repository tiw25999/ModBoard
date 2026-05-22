import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fastapi import Response

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
from app.services.auth import ADMIN_COOKIE, require_admin
from app.services.poller import poll_once

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
# Routes that should NOT trigger an auth prompt (logout, etc.) live on a
# separate router so they bypass the require_admin dependency.
public_admin_router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")


@public_admin_router.get("/logout")
async def logout():
    """Best-effort admin logout. Clears the UI marker cookie and returns a
    page that explains the Basic-auth limitation (browser must drop creds
    on its own — usually by closing the tab)."""
    response = RedirectResponse("/?logged_out=1", status_code=303)
    response.delete_cookie(ADMIN_COOKIE, samesite="lax")
    return response


async def _count(session: AsyncSession, model) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar() or 0)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: AsyncSession = Depends(get_db)):
    """Admin landing — KPIs across the whole site + recent activity feed."""
    totals = {
        "mods": await _count(session, Mod),
        "snapshots": await _count(session, ModSnapshot),
        "comments": await _count(session, ModComment),
        "discussions": await _count(session, ModDiscussion),
        "changelogs": await _count(session, ModChangelog),
        "threads": await _count(session, ForumThread),
        "replies": await _count(session, ForumPost),
    }

    # Live aggregates from latest snapshot per mod
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

    # Recent activity — union sample of all event-bearing rows
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
    """Fire a poll immediately. Runs in background — admin sees the new
    snapshots on the next dashboard refresh."""
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
