from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import or_

from app.db import get_db
from app.models import (
    ForumPost,
    ForumThread,
    Mod,
    ModChangelog,
    ModComment,
    ModDiscussion,
    ModSnapshot,
    NewsPost,
    RoadmapItem,
    User,
)
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
        "roadmap": await _n(RoadmapItem),
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


@router.get("/mod/{mod_id}/roadmap", response_class=HTMLResponse)
async def mod_roadmap(
    request: Request, mod_id: int, session: AsyncSession = Depends(get_db)
):
    mod = await _require_mod(session, mod_id)
    items = (
        await session.execute(
            select(RoadmapItem)
            .where(RoadmapItem.mod_id == mod_id)
            .order_by(RoadmapItem.position.asc(), RoadmapItem.id.asc())
        )
    ).scalars().all()
    counts = await _counts(session, mod_id)
    # Group by status for easy column rendering
    by_status: dict[str, list] = {"in_progress": [], "planned": [], "done": [], "cancelled": []}
    for it in items:
        by_status.setdefault(it.status, []).append(it)
    return templates.TemplateResponse(
        request, "mod_roadmap.html",
        {"mod": mod, "items": items, "by_status": by_status,
         "counts": counts, "active_tab": "roadmap"},
    )


@router.get("/news", response_class=HTMLResponse)
async def news_index(request: Request, session: AsyncSession = Depends(get_db)):
    posts = (
        await session.execute(
            select(NewsPost)
            .where(NewsPost.active.is_(True))
            .order_by(NewsPost.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return templates.TemplateResponse(
        request, "news.html", {"posts": posts},
    )


@router.get("/donate", response_class=HTMLResponse)
async def donate(request: Request):
    return templates.TemplateResponse(request, "donate.html", {})


@router.get("/u/{user_id:int}", response_class=HTMLResponse)
async def public_user_profile(
    request: Request,
    user_id: int,
    session: AsyncSession = Depends(get_db),
):
    """Public profile for any registered user — shows what they posted in
    the forum + cumulative stats. Email is never exposed."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404)

    from sqlalchemy import func as _func, desc as _desc

    threads = (
        await session.execute(
            select(ForumThread)
            .where(ForumThread.author_user_id == user_id)
            .order_by(_desc(ForumThread.created_at))
            .limit(50)
        )
    ).scalars().all()
    replies = (
        await session.execute(
            select(ForumPost)
            .where(ForumPost.author_user_id == user_id)
            .order_by(_desc(ForumPost.created_at))
            .limit(50)
        )
    ).scalars().all()
    upvotes_received = int(
        (await session.execute(
            select(_func.coalesce(_func.sum(ForumThread.upvotes), 0))
            .where(ForumThread.author_user_id == user_id)
        )).scalar() or 0
    )
    # Resolve thread titles for reply rendering
    reply_thread_ids = {p.thread_id for p in replies}
    thread_lookup = {}
    if reply_thread_ids:
        rows = (await session.execute(
            select(ForumThread).where(ForumThread.id.in_(reply_thread_ids))
        )).scalars().all()
        thread_lookup = {t.id: t for t in rows}

    return templates.TemplateResponse(
        request, "user_profile.html",
        {
            "profile": user,
            "threads": threads,
            "replies": replies,
            "thread_lookup": thread_lookup,
            "upvotes_received": upvotes_received,
        },
    )


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = Query(default=""),
    session: AsyncSession = Depends(get_db),
):
    """Cross-system search: mods + forum threads + comments + discussions + changelogs.
    Plain ILIKE for now — switch to Postgres FTS later if traffic grows."""
    q = q.strip()
    results = {"mods": [], "threads": [], "comments": [], "discussions": [], "changelogs": []}
    if q and len(q) >= 2:
        like = f"%{q}%"

        results["mods"] = (
            await session.execute(
                select(Mod)
                .where(
                    Mod.public.is_(True),
                    or_(
                        Mod.name.ilike(like),
                        Mod.title.ilike(like),
                        Mod.description.ilike(like),
                        Mod.app_name.ilike(like),
                    ),
                )
                .order_by(Mod.name)
                .limit(30)
            )
        ).scalars().all()

        from sqlalchemy.orm import selectinload as _sel
        results["threads"] = (
            await session.execute(
                select(ForumThread)
                .options(_sel(ForumThread.mod))
                .where(or_(
                    ForumThread.title.ilike(like),
                    ForumThread.body_raw.ilike(like),
                    ForumThread.author_name.ilike(like),
                ))
                .order_by(ForumThread.last_post_at.desc())
                .limit(30)
            )
        ).scalars().all()

        results["comments"] = (
            await session.execute(
                select(ModComment)
                .options(_sel(ModComment.mod))
                .where(or_(
                    ModComment.body_html.ilike(like),
                    ModComment.author_name.ilike(like),
                ))
                .order_by(ModComment.posted_at.desc().nulls_last())
                .limit(30)
            )
        ).scalars().all()

        results["discussions"] = (
            await session.execute(
                select(ModDiscussion)
                .options(_sel(ModDiscussion.mod))
                .where(or_(
                    ModDiscussion.title.ilike(like),
                    ModDiscussion.body_preview.ilike(like),
                    ModDiscussion.author_name.ilike(like),
                ))
                .order_by(ModDiscussion.last_post_at.desc().nulls_last())
                .limit(30)
            )
        ).scalars().all()

        results["changelogs"] = (
            await session.execute(
                select(ModChangelog)
                .options(_sel(ModChangelog.mod))
                .where(or_(
                    ModChangelog.body_html.ilike(like),
                    ModChangelog.headline.ilike(like),
                ))
                .order_by(ModChangelog.posted_at.desc().nulls_last())
                .limit(30)
            )
        ).scalars().all()

    total = sum(len(v) for v in results.values())
    return templates.TemplateResponse(
        request, "search.html",
        {"q": q, "results": results, "total": total},
    )


@router.get("/dashboard", response_class=HTMLResponse)
async def overview_dashboard(
    request: Request,
    game: str | None = Query(default=None),
    metric: str = Query(default="subscribers"),
    days: int = Query(default=14, ge=1, le=180),
    session: AsyncSession = Depends(get_db),
):
    """Overlay each tracked mod's subscriber/comment/visitor curve on a single chart."""
    metric = metric if metric in ("subscribers", "visitors", "favorites", "comments") else "subscribers"

    mods_q = select(Mod).where(Mod.public.is_(True)).order_by(Mod.app_name.nulls_last(), Mod.name)
    if game:
        mods_q = mods_q.where(Mod.app_name == game)
    mods = (await session.execute(mods_q)).scalars().all()

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    field_map = {
        "subscribers": ModSnapshot.subscribers_display,
        "visitors":    ModSnapshot.visitors_display,
        "favorites":   ModSnapshot.favorites_display,
        "comments":    ModSnapshot.comments_count,
    }
    field = field_map[metric]

    series: list[dict] = []
    for m in mods:
        snaps = (
            await session.execute(
                select(ModSnapshot.captured_at, field)
                .where(ModSnapshot.mod_id == m.id, ModSnapshot.captured_at >= cutoff)
                .order_by(ModSnapshot.captured_at.asc())
            )
        ).all()
        if not snaps:
            continue
        series.append({
            "mod_id": m.id,
            "label": m.title or m.name,
            "app_name": m.app_name,
            "points": [
                {"t": ts.isoformat(), "v": int(v) if v is not None else None}
                for ts, v in snaps
            ],
        })

    all_games = sorted({m.app_name for m in (
        await session.execute(select(Mod).where(Mod.public.is_(True)))
    ).scalars().all() if m.app_name})

    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "series": series,
            "metric": metric,
            "days": days,
            "active_game": game,
            "all_games": all_games,
            "mod_count": len(series),
        },
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
