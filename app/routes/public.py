from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Mod, ModChangelog, ModComment, ModDiscussion, ModSnapshot
from app.services.bbcode import steam_bbcode_to_html

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


async def _require_mod(session: AsyncSession, mod_id: int) -> Mod:
    mod = await session.get(Mod, mod_id)
    if mod is None or not mod.public:
        raise HTTPException(404)
    return mod


async def _counts(session: AsyncSession, mod_id: int) -> dict[str, int]:
    """Lightweight counts for nav badges on the detail page."""
    from sqlalchemy import func

    async def _n(model) -> int:
        q = select(func.count()).select_from(model).where(model.mod_id == mod_id)
        return int((await session.execute(q)).scalar() or 0)

    return {
        "comments": await _n(ModComment),
        "changelogs": await _n(ModChangelog),
        "discussions": await _n(ModDiscussion),
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, session: AsyncSession = Depends(get_db)):
    mods = (
        await session.execute(
            select(Mod)
            .where(Mod.public.is_(True))
            .order_by(Mod.app_name.nulls_last(), Mod.name)
        )
    ).scalars().all()
    # Group by parent app/game. Mods whose app_name hasn't been resolved yet
    # land under "Unknown" so they remain visible.
    games: "OrderedDict[str, list[dict]]" = OrderedDict()
    for m in mods:
        bucket = m.app_name or "Unknown game"
        snap = await _latest_snapshot(session, m.id)
        games.setdefault(bucket, []).append({"mod": m, "snap": snap})
    return templates.TemplateResponse(
        request, "mod_list.html",
        {"games": games, "total_mods": len(mods)},
    )


@router.get("/mod/{mod_id}", response_class=HTMLResponse)
async def mod_detail(
    request: Request, mod_id: int, session: AsyncSession = Depends(get_db)
):
    mod = await _require_mod(session, mod_id)
    snap = await _latest_snapshot(session, mod_id)
    # Detail page only renders a small preview of each list. Full lists live
    # on their own /comments, /changelog, /discussions pages.
    recent_comments = (
        await session.execute(
            select(ModComment)
            .where(ModComment.mod_id == mod_id)
            .order_by(ModComment.posted_at.desc().nulls_last())
            .limit(3)
        )
    ).scalars().all()
    counts = await _counts(session, mod_id)
    return templates.TemplateResponse(
        request, "mod_detail.html",
        {
            "mod": mod,
            "snap": snap,
            "recent_comments": recent_comments,
            "counts": counts,
            "description_html": steam_bbcode_to_html(mod.description),
        },
    )


@router.get("/mod/{mod_id}/comments", response_class=HTMLResponse)
async def mod_comments(
    request: Request, mod_id: int, session: AsyncSession = Depends(get_db)
):
    mod = await _require_mod(session, mod_id)
    comments = (
        await session.execute(
            select(ModComment)
            .where(ModComment.mod_id == mod_id)
            .order_by(ModComment.posted_at.desc().nulls_last())
            .limit(200)
        )
    ).scalars().all()
    counts = await _counts(session, mod_id)
    return templates.TemplateResponse(
        request, "mod_comments.html",
        {"mod": mod, "comments": comments, "counts": counts, "active_tab": "comments"},
    )


@router.get("/mod/{mod_id}/changelog", response_class=HTMLResponse)
async def mod_changelog(
    request: Request, mod_id: int, session: AsyncSession = Depends(get_db)
):
    mod = await _require_mod(session, mod_id)
    changelogs = (
        await session.execute(
            select(ModChangelog)
            .where(ModChangelog.mod_id == mod_id)
            .order_by(ModChangelog.posted_at.desc().nulls_last())
            .limit(200)
        )
    ).scalars().all()
    counts = await _counts(session, mod_id)
    return templates.TemplateResponse(
        request, "mod_changelog.html",
        {"mod": mod, "changelogs": changelogs, "counts": counts, "active_tab": "changelog"},
    )


@router.get("/mod/{mod_id}/discussions", response_class=HTMLResponse)
async def mod_discussions(
    request: Request, mod_id: int, session: AsyncSession = Depends(get_db)
):
    mod = await _require_mod(session, mod_id)
    discussions = (
        await session.execute(
            select(ModDiscussion)
            .where(ModDiscussion.mod_id == mod_id)
            .order_by(ModDiscussion.last_post_at.desc().nulls_last())
            .limit(200)
        )
    ).scalars().all()
    counts = await _counts(session, mod_id)
    return templates.TemplateResponse(
        request, "mod_discussions.html",
        {"mod": mod, "discussions": discussions, "counts": counts, "active_tab": "discussions"},
    )


@router.get("/mod/{mod_id}/stats", response_class=HTMLResponse)
async def mod_stats(
    request: Request, mod_id: int, session: AsyncSession = Depends(get_db)
):
    mod = await _require_mod(session, mod_id)
    q = (
        select(ModSnapshot)
        .where(ModSnapshot.mod_id == mod_id)
        .order_by(ModSnapshot.captured_at.asc())
    )
    snaps = (await session.execute(q)).scalars().all()
    labels = [s.captured_at.strftime("%Y-%m-%d %H:%M") for s in snaps]
    subs = [s.subscribers_display for s in snaps]
    counts = await _counts(session, mod_id)
    return templates.TemplateResponse(
        request, "mod_stats.html",
        {"mod": mod, "labels": labels, "subs": subs, "counts": counts, "active_tab": "stats"},
    )
