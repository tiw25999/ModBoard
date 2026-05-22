from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import func, or_

from app.db import get_db
from app.models import (
    ForumPost,
    ForumThread,
    Mod,
    ModChangelog,
    ModComment,
    ModDiscussion,
    ModSnapshot,
    ModSubscription,
    NewsPost,
    RoadmapItem,
    User,
)
from app.services.session import current_user
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
    recent_comments = (
        await session.execute(
            select(ModComment)
            .where(ModComment.mod_id == mod_id)
            .order_by(ModComment.posted_at.desc().nulls_last())
            .limit(3)
        )
    ).scalars().all()
    counts = await _counts(session, mod_id)

    # Subscription state — only relevant to logged-in users
    is_subscribed = False
    user = await current_user(request, session)
    if user is not None:
        is_subscribed = (await session.execute(
            select(ModSubscription).where(
                ModSubscription.user_id == user.id,
                ModSubscription.mod_id == mod_id,
            )
        )).scalar_one_or_none() is not None
    sub_count = int((await session.execute(
        select(func.count()).select_from(ModSubscription).where(ModSubscription.mod_id == mod_id)
    )).scalar() or 0)

    return templates.TemplateResponse(
        request, "mod_detail.html",
        {
            "mod": mod,
            "snap": snap,
            "recent_comments": recent_comments,
            "counts": counts,
            "description_html": steam_bbcode_to_html(mod.description),
            "is_subscribed": is_subscribed,
            "sub_count": sub_count,
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


@router.post("/mod/{mod_id}/subscribe")
async def toggle_mod_subscription(
    request: Request,
    mod_id: int,
    session: AsyncSession = Depends(get_db),
):
    """Toggle a user's follow on a mod. Anon users get bounced to login.
    Acts as the same endpoint for subscribe + unsubscribe (idempotent)."""
    user = await current_user(request, session)
    if user is None:
        return RedirectResponse(f"/auth/login?next=/mod/{mod_id}", status_code=303)
    mod = await session.get(Mod, mod_id)
    if mod is None:
        raise HTTPException(404)
    existing = (
        await session.execute(
            select(ModSubscription).where(
                ModSubscription.user_id == user.id,
                ModSubscription.mod_id == mod_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
    else:
        session.add(ModSubscription(
            user_id=user.id, mod_id=mod_id,
            created_at=datetime.now(timezone.utc),
        ))
    await session.commit()
    referer = request.headers.get("referer", f"/mod/{mod_id}")
    return RedirectResponse(referer, status_code=303)


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
    """Cross-system search using Postgres full-text search.

    Strategy:
      - Build a Postgres `tsquery` from the user's input via `websearch_to_tsquery`
        (supports natural-language input: bare words, "exact phrase", -exclude).
      - Each row type has a concatenated text expression we run `to_tsvector`
        on at query time. With small tables this is fine; once a table grows
        past ~10k rows, add a STORED generated tsvector column + GIN index
        per table to keep the planner fast.
      - Fall back to ILIKE on very short queries (1 char) since FTS requires
        meaningful tokens.
    """
    from sqlalchemy import func as _func, literal, type_coerce
    from sqlalchemy.dialects.postgresql import TSVECTOR

    q = q.strip()
    results = {"mods": [], "threads": [], "comments": [], "discussions": [], "changelogs": []}
    if not (q and len(q) >= 2):
        total = 0
        return templates.TemplateResponse(
            request, "search.html",
            {"q": q, "results": results, "total": total},
        )

    # websearch_to_tsquery is forgiving — accepts bare user input safely
    tsq = _func.websearch_to_tsquery("english", q)

    def _vec(*cols):
        """Build to_tsvector('english', col1 || ' ' || col2 || ...).
        coalesce NULLs to '' so a missing field doesn't nuke the row."""
        expr = cols[0]
        for c in cols[1:]:
            expr = expr.op("||")(literal(" ")).op("||")(c)
        return _func.to_tsvector("english", _func.coalesce(expr, ""))

    from sqlalchemy.orm import selectinload as _sel

    # --- Mods ---
    mods_vec = _vec(
        _func.coalesce(Mod.name, ""),
        _func.coalesce(Mod.title, ""),
        _func.coalesce(Mod.description, ""),
        _func.coalesce(Mod.app_name, ""),
    )
    results["mods"] = (
        await session.execute(
            select(Mod)
            .where(Mod.public.is_(True), mods_vec.op("@@")(tsq))
            .order_by(_func.ts_rank(mods_vec, tsq).desc(), Mod.name)
            .limit(30)
        )
    ).scalars().all()

    # --- Forum threads ---
    threads_vec = _vec(
        _func.coalesce(ForumThread.title, ""),
        _func.coalesce(ForumThread.body_raw, ""),
        _func.coalesce(ForumThread.author_name, ""),
    )
    results["threads"] = (
        await session.execute(
            select(ForumThread)
            .options(_sel(ForumThread.mod))
            .where(threads_vec.op("@@")(tsq))
            .order_by(_func.ts_rank(threads_vec, tsq).desc(), ForumThread.last_post_at.desc())
            .limit(30)
        )
    ).scalars().all()

    # --- Workshop comments ---
    comments_vec = _vec(
        _func.coalesce(ModComment.body_html, ""),
        _func.coalesce(ModComment.author_name, ""),
    )
    results["comments"] = (
        await session.execute(
            select(ModComment)
            .options(_sel(ModComment.mod))
            .where(comments_vec.op("@@")(tsq))
            .order_by(_func.ts_rank(comments_vec, tsq).desc(), ModComment.posted_at.desc().nulls_last())
            .limit(30)
        )
    ).scalars().all()

    # --- Steam discussions ---
    disc_vec = _vec(
        _func.coalesce(ModDiscussion.title, ""),
        _func.coalesce(ModDiscussion.body_preview, ""),
        _func.coalesce(ModDiscussion.author_name, ""),
    )
    results["discussions"] = (
        await session.execute(
            select(ModDiscussion)
            .options(_sel(ModDiscussion.mod))
            .where(disc_vec.op("@@")(tsq))
            .order_by(_func.ts_rank(disc_vec, tsq).desc(), ModDiscussion.last_post_at.desc().nulls_last())
            .limit(30)
        )
    ).scalars().all()

    # --- Changelogs ---
    cl_vec = _vec(
        _func.coalesce(ModChangelog.headline, ""),
        _func.coalesce(ModChangelog.body_html, ""),
    )
    results["changelogs"] = (
        await session.execute(
            select(ModChangelog)
            .options(_sel(ModChangelog.mod))
            .where(cl_vec.op("@@")(tsq))
            .order_by(_func.ts_rank(cl_vec, tsq).desc(), ModChangelog.posted_at.desc().nulls_last())
            .limit(30)
        )
    ).scalars().all()

    total = sum(len(v) for v in results.values())
    return templates.TemplateResponse(
        request, "search.html",
        {"q": q, "results": results, "total": total},
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
