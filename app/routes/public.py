from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
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
    ModFile,
    ModSnapshot,
    ModSubscription,
    NewsPost,
    RoadmapItem,
    User,
)
from app.services import mod_files as mf
from app.services.cache import mark_seen
from app.services.ratelimit import client_ip
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


async def _latest_snapshots_by_mod(
    session: AsyncSession, mod_ids: list[int]
) -> dict[int, ModSnapshot]:
    """One Postgres-flavoured query (`DISTINCT ON`) instead of N+1 — used
    on the homepage where we render `len(mods)` rows and previously fired
    one snapshot SELECT per row. Goes from N+1 → 1 trip."""
    if not mod_ids:
        return {}
    q = (
        select(ModSnapshot)
        .where(ModSnapshot.mod_id.in_(mod_ids))
        .order_by(ModSnapshot.mod_id, ModSnapshot.captured_at.desc())
        .distinct(ModSnapshot.mod_id)
    )
    rows = (await session.execute(q)).scalars().all()
    return {s.mod_id: s for s in rows}


async def _require_mod(session: AsyncSession, mod_id: int) -> Mod:
    mod = await session.get(Mod, mod_id)
    if mod is None or not mod.public:
        raise HTTPException(404)
    return mod


async def _current_file(session: AsyncSession, mod_id: int) -> ModFile | None:
    return (await session.execute(
        select(ModFile).where(ModFile.mod_id == mod_id, ModFile.is_current.is_(True))
        .limit(1)
    )).scalar_one_or_none()


def base_version(version: str) -> str:
    """Group key for a file's version label: strip any parenthetical
    format note so "1.0 (Installer .exe)" and "1.0 (ZIP)" both collapse
    to "1.0" — the same release shown in two formats."""
    return (version or "").split("(")[0].strip() or (version or "")


_FORMAT_LABELS = {
    "exe": "Installer (.exe)",
    "zip": "ZIP (.zip)",
    "7z": "7-Zip (.7z)",
    "rar": "RAR (.rar)",
    "apk": "Android (.apk)",
    "dmg": "macOS (.dmg)",
}


def format_label(filename: str) -> str:
    """Human download-format label derived from the file extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    return _FORMAT_LABELS.get(ext, (ext.upper() + " file") if ext else "Download")


def group_versions(files: list[ModFile]) -> list[dict]:
    """Collapse ModFile rows into one entry per release (by base version),
    each carrying its download formats. Newest release first."""
    from collections import OrderedDict
    groups: "OrderedDict[str, dict]" = OrderedDict()
    for f in files:
        key = base_version(f.version)
        g = groups.get(key)
        if g is None:
            g = {"version": key, "files": [], "uploaded_at": f.uploaded_at, "is_current": False}
            groups[key] = g
        g["files"].append({"file": f, "format": format_label(f.filename)})
        if f.is_current:
            g["is_current"] = True
        if f.uploaded_at and (g["uploaded_at"] is None or f.uploaded_at > g["uploaded_at"]):
            g["uploaded_at"] = f.uploaded_at
    # Within each release, show the current file first, then newest-first.
    for g in groups.values():
        g["files"].sort(
            key=lambda it: (not it["file"].is_current, -it["file"].uploaded_at.timestamp())
        )
    return list(groups.values())


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
        "files": await _n(ModFile),
    }


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    game: str = Query(default="", max_length=256),
    session: AsyncSession = Depends(get_db),
):
    mods = (
        await session.execute(
            select(Mod)
            .where(Mod.public.is_(True))
            .order_by(Mod.app_name.nulls_last(), Mod.name)
        )
    ).scalars().all()
    # One bulk query for snapshots instead of N+1 — page load on a 100-mod
    # homepage drops from ~100 round trips to 2 (mods + snapshots).
    snaps = await _latest_snapshots_by_mod(session, [m.id for m in mods])

    def _bucket(m: Mod) -> str:
        # Steam mods group by app_name; manual mods by game_name.
        return (m.app_name if m.source != "manual" else m.game_name) or "Unknown game"

    # Full list of game names for the filter chips (before filtering).
    all_games = sorted({_bucket(m) for m in mods})

    # Group by parent app/game. Mods whose app_name hasn't been resolved yet
    # land under "Unknown" so they remain visible.
    game = game.strip()
    games: "OrderedDict[str, list[dict]]" = OrderedDict()
    shown = 0
    for m in mods:
        bucket = _bucket(m)
        if game and bucket != game:
            continue
        games.setdefault(bucket, []).append({"mod": m, "snap": snaps.get(m.id)})
        shown += 1

    return templates.TemplateResponse(
        request, "mod_list.html",
        {"games": games, "total_mods": shown,
         "all_games": all_games, "active_game": game},
    )


@router.get("/mod/{mod_id}", response_class=HTMLResponse)
async def mod_detail(
    request: Request, mod_id: int, session: AsyncSession = Depends(get_db)
):
    mod = await _require_mod(session, mod_id)
    # Manual mods track their own page views (Steam mods use Steam visitor data).
    if mod.source == "manual":
        if mark_seen(f"view:{mod_id}:{client_ip(request)}", 86400):
            mod.view_count = (mod.view_count or 0) + 1
            await session.commit()
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

    current_file = await _current_file(session, mod_id) if mod.source == "manual" else None

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
            "current_file": current_file,
        },
    )


@router.get("/mod/{mod_id}/download")
async def mod_download_current(
    request: Request, mod_id: int, session: AsyncSession = Depends(get_db)
):
    mod = await _require_mod(session, mod_id)
    f = await _current_file(session, mod_id)
    if f is None:
        raise HTTPException(404, detail="no file available")
    return await _serve_file(request, session, mod, f)


@router.get("/mod/{mod_id}/download/{file_id}")
async def mod_download_version(
    request: Request, mod_id: int, file_id: int,
    session: AsyncSession = Depends(get_db),
):
    mod = await _require_mod(session, mod_id)
    f = await session.get(ModFile, file_id)
    if f is None or f.mod_id != mod_id:
        raise HTTPException(404)
    return await _serve_file(request, session, mod, f)


async def _serve_file(request: Request, session: AsyncSession, mod: Mod, f: ModFile):
    try:
        path = mf.resolve_download_path(f.stored_path)
    except ValueError:
        raise HTTPException(404)
    if not path.exists():
        raise HTTPException(404, detail="file missing from storage")

    # Count once per IP+file per 24h. Best-effort; never block the download.
    ip = client_ip(request)
    if mark_seen(f"dl:{f.id}:{ip}", 86400):
        f.download_count = (f.download_count or 0) + 1
        mod.download_count = (mod.download_count or 0) + 1
        await session.commit()

    # Sanitize filename for the header (strip quotes/newlines).
    safe_name = "".join(c for c in f.filename if c.isprintable() and c not in '"\r\n') or "download"
    return FileResponse(
        path,
        media_type=f.content_type or "application/octet-stream",
        filename=safe_name,  # FileResponse emits Content-Disposition: attachment
    )


@router.get("/mod/{mod_id}/versions", response_class=HTMLResponse)
async def mod_versions(
    request: Request, mod_id: int, session: AsyncSession = Depends(get_db)
):
    mod = await _require_mod(session, mod_id)
    files = (await session.execute(
        select(ModFile).where(ModFile.mod_id == mod_id)
        .order_by(ModFile.uploaded_at.desc())
    )).scalars().all()
    versions = group_versions(list(files))
    counts = await _counts(session, mod_id)
    return templates.TemplateResponse(
        request, "mod_versions.html",
        {"mod": mod, "versions": versions, "counts": counts, "active_tab": "versions"},
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
async def donate(request: Request, session: AsyncSession = Depends(get_db)):
    from app.models.membership import MembershipTier
    from app.services.membership import active_membership as _active_mem

    tiers = (await session.execute(
        select(MembershipTier).where(MembershipTier.active.is_(True))
        .order_by(MembershipTier.price_usd_cents)
    )).scalars().all()

    user_state = request.state.user
    current_mem = None
    if user_state:
        current_mem = await _active_mem(session, user_state["id"])
        if current_mem:
            await session.refresh(current_mem, ["tier"])

    return templates.TemplateResponse(request, "donate.html", {
        "tiers": tiers,
        "current_mem": current_mem,
        "error": request.query_params.get("error"),
    })


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    """Static privacy policy page — required for EU users (ePrivacy
    + GDPR) and good citizenship anywhere else."""
    return templates.TemplateResponse(request, "privacy.html", {})


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
    # Same-origin redirect target only — never trust the Referer header
    # as a redirect destination (open redirect → phishing primitive).
    # Mirrors the _safe_referer pattern from app/routes/forum.py.
    ref = request.headers.get("referer", "")
    target = f"/mod/{mod_id}"
    if ref.startswith("/") and not ref.startswith("//"):
        if not ref.startswith("/admin") and not ref.startswith("/auth/"):
            target = ref
    else:
        from urllib.parse import urlparse
        parsed = urlparse(ref)
        if (parsed.scheme in ("http", "https") and parsed.netloc == request.url.netloc
                and not parsed.path.startswith(("/admin", "/auth/"))):
            target = parsed.path or target
    return RedirectResponse(target, status_code=303)


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
    q: str = Query(default="", max_length=200),
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
