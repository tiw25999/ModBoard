"""Anonymous community forum — global, threads optionally tag a mod."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.services.membership import vote_weight_for
from app.models import (
    REACTION_EMOJIS,
    THREAD_KINDS,
    THREAD_STATUSES,
    ForumPost,
    ForumReaction,
    ForumThread,
    ForumUpvote,
    Mod,
    Notification,
    User,
)
from app.services.anon import get_or_create_token, get_token
from app.services.auth import admin_marker_present, is_admin as _check_admin
from app.services.session import current_user
from app.services.textfmt import extract_mentions, is_likely_spam, render, slugify


# Names that should never resolve to a real user account when an `@mention`
# is being processed — they're trust signals in the UI, not handles. A user
# who happens to be named "developer" on Google will not intercept `@Developer`
# pings meant for the staff.
_RESERVED_MENTION_NAMES = {"developer", "admin", "moderator", "mod", "system", "staff"}


async def _notify_mentions(session, raw_body: str, thread_id: int, post_id: int | None,
                            actor_name: str, exclude_user_id: int | None) -> None:
    """Create notifications for any @mentioned users (case-insensitive match
    on user.name). Doesn't notify the actor themselves. Reserved names are
    silently dropped so they can't be hijacked by signups."""
    names = [n for n in extract_mentions(raw_body) if n.lower() not in _RESERVED_MENTION_NAMES]
    if not names:
        return
    from sqlalchemy import func as _func
    targets = (await session.execute(
        select(User).where(_func.lower(User.name).in_([n.lower() for n in names]))
    )).scalars().all()
    if not targets:
        return
    now = datetime.now(timezone.utc)
    for u in targets:
        if exclude_user_id and u.id == exclude_user_id:
            continue
        # Belt + braces: a user whose stored name happens to match a
        # reserved label (legacy data, race) is also dropped here.
        if u.name and u.name.lower() in _RESERVED_MENTION_NAMES:
            continue
        session.add(Notification(
            user_id=u.id,
            kind="mention",
            thread_id=thread_id,
            post_id=post_id,
            actor_name=actor_name,
            payload=raw_body[:200],
            created_at=now,
        ))

DEVELOPER_LABEL = "Developer"

# Caps for spam / DoS resistance.
MAX_BODY_LEN = 20000          # truncate any body before persist
MAX_TITLE_LEN = 256
MAX_AUTHOR_LEN = 64
MAX_FANOUT_PER_POST = 100     # how many reply-to-reply notifications a single post can fire
RATE_LIMIT_REACTIONS_PER_HOUR = 120


def _safe_referer(request: Request, fallback: str = "/forum") -> str:
    """Treat the Referer header as untrusted user input. Accept only a
    same-origin non-admin path; anything else collapses to fallback.
    Used by endpoints (e.g. /forum/react) that redirect back to wherever
    the user came from.

    /admin/* is rejected too — an attacker setting Referer to an admin
    URL on a victim's reaction click would reflect that admin URL via
    the 303 → /auth/login bounce, leaking the existence of a specific
    admin path the user was probing.
    """
    ref = request.headers.get("referer", "")
    if not ref:
        return fallback
    # Resolve to a same-origin path
    target = None
    if ref.startswith("/") and not ref.startswith("//"):
        target = ref
    else:
        from urllib.parse import urlparse
        parsed = urlparse(ref)
        if parsed.scheme in ("http", "https") and parsed.netloc == request.url.netloc:
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
    if target is None:
        return fallback
    # Strip admin paths to avoid leaking what the user was trying to reach
    if target.startswith("/admin") or target.startswith("/auth/"):
        return fallback
    return target


def require_admin_or_403(request: Request):
    """Replacement for the old Basic-auth Depends. Throws 403 on miss
    instead of triggering a Basic prompt — callers reach these via
    in-page admin forms, and the login flow lives on /auth/login."""
    if not _check_admin(request):
        raise HTTPException(403, detail="Admin only — sign in at /auth/login")

router = APIRouter(prefix="/forum")
templates = Jinja2Templates(directory="app/templates")

POST_WINDOW = timedelta(minutes=15)
RATE_LIMIT_THREADS_PER_HOUR = 5
RATE_LIMIT_REPLIES_PER_HOUR = 25


# ---------- helpers --------------------------------------------------------

async def _check_rate_limit(
    session: AsyncSession, token: str, model, limit: int
) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    q = (
        select(func.count())
        .select_from(model)
        .where(model.author_token == token, model.created_at >= cutoff)
    )
    n = int((await session.execute(q)).scalar() or 0)
    if n >= limit:
        raise HTTPException(429, detail="You're posting too fast. Try again in a bit.")


def _is_admin(request: Request) -> bool:
    """UI-level admin check used for: showing admin action buttons,
    deciding whether to override author name to "Developer", etc.
    Trust here is just for display labelling — the actual destructive
    actions (pin/lock/status/delete) still go through require_admin
    which re-verifies Basic auth on the request."""
    return admin_marker_present(request)


# ---------- list -----------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def forum_list(
    request: Request,
    response: Response,
    kind: str | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_db),
):
    get_or_create_token(request, response)
    q = (
        select(ForumThread)
        .options(selectinload(ForumThread.mod))
        .order_by(ForumThread.pinned.desc(), ForumThread.last_post_at.desc())
    )
    if kind in THREAD_KINDS:
        q = q.where(ForumThread.kind == kind)
    if status in THREAD_STATUSES:
        q = q.where(ForumThread.status == status)
    threads = (await session.execute(q.limit(100))).scalars().all()
    counts = {
        "all": int((await session.execute(select(func.count()).select_from(ForumThread))).scalar() or 0),
    }
    for k in THREAD_KINDS:
        counts[k] = int(
            (await session.execute(
                select(func.count()).select_from(ForumThread).where(ForumThread.kind == k)
            )).scalar() or 0
        )

    # Preload membership emoji for thread authors.
    from app.models.membership import UserMembership, MembershipTier as _MT2
    _list_author_ids = {t.author_user_id for t in threads if t.author_user_id}
    list_member_emojis: dict[int, str] = {}
    if _list_author_ids:
        _lnow = datetime.now(timezone.utc)
        _lrows = (await session.execute(
            select(UserMembership)
            .join(_MT2, UserMembership.tier_id == _MT2.id)
            .where(
                UserMembership.user_id.in_(_list_author_ids),
                UserMembership.expires_at > _lnow,
            )
            .order_by(UserMembership.expires_at.desc())
        )).scalars().all()
        _lseen: set[int] = set()
        for _lm in _lrows:
            if _lm.user_id not in _lseen:
                await session.refresh(_lm, ["tier"])
                list_member_emojis[_lm.user_id] = _lm.tier.emoji
                _lseen.add(_lm.user_id)

    return templates.TemplateResponse(
        request, "forum_list.html",
        {
            "threads": threads,
            "counts": counts,
            "active_kind": kind,
            "active_status": status,
            "kinds": THREAD_KINDS,
            "statuses": THREAD_STATUSES,
            "member_emojis": list_member_emojis,
        },
    )


# ---------- new thread form ------------------------------------------------

@router.get("/new", response_class=HTMLResponse)
async def forum_new_form(
    request: Request,
    response: Response,
    mod: int | None = None,
    session: AsyncSession = Depends(get_db),
):
    get_or_create_token(request, response)
    mods = (
        await session.execute(
            select(Mod).where(Mod.public.is_(True)).order_by(Mod.app_name.nulls_last(), Mod.name)
        )
    ).scalars().all()
    return templates.TemplateResponse(
        request, "forum_new.html",
        {
            "mods": mods,
            "preselected_mod": mod,
            "kinds": THREAD_KINDS,
            "form": {},
            "error": None,
            "is_admin": _is_admin(request),
        },
    )


@router.post("/new")
async def forum_new_submit(
    request: Request,
    response: Response,
    title: str = Form(...),
    body: str = Form(...),
    author_name: str = Form(...),
    kind: str = Form("discussion"),
    mod_id: str = Form(""),
    website: str = Form(""),  # honeypot
    session: AsyncSession = Depends(get_db),
):
    token = get_or_create_token(request, response)
    title = title.strip()[:MAX_TITLE_LEN]
    body = body.strip()[:MAX_BODY_LEN]
    kind = kind if kind in THREAD_KINDS else "discussion"
    user = await current_user(request, session)
    # Admins post under a fixed "Developer" label.
    # Logged-in non-admins use their Google profile name (and get user_id set).
    # Anon posters supply their own name.
    if _is_admin(request):
        author_name = DEVELOPER_LABEL
    elif user is not None:
        # Never fall back to the email local-part for the public byline
        # — that would publish PII (even partial) in JSON-LD, RSS, etc.
        author_name = (user.name or f"user{user.id}")[:64]
    else:
        author_name = author_name.strip()[:64]
    # Never let a non-admin masquerade as Developer (the label is a
    # trust signal in the UI; impersonating it is account-fraud-tier).
    if not _is_admin(request) and author_name.strip().lower() == DEVELOPER_LABEL.lower():
        author_name = "anon"

    reason = is_likely_spam(title, body, author_name, website)
    if reason:
        mods = (await session.execute(
            select(Mod).where(Mod.public.is_(True)).order_by(Mod.app_name.nulls_last(), Mod.name)
        )).scalars().all()
        return templates.TemplateResponse(
            request, "forum_new.html",
            {
                "mods": mods,
                "preselected_mod": None,
                "kinds": THREAD_KINDS,
                "form": {"title": title, "body": body, "author_name": author_name, "kind": kind, "mod_id": mod_id},
                "error": reason,
                "is_admin": _is_admin(request),
            },
            status_code=400,
        )
    await _check_rate_limit(session, token, ForumThread, RATE_LIMIT_THREADS_PER_HOUR)

    parsed_mod_id: int | None = None
    if mod_id.strip().isdigit():
        candidate = int(mod_id)
        exists = await session.get(Mod, candidate)
        # Only allow tagging the thread to a publicly-visible mod —
        # threads tagged to a private mod would render a broken link
        # for everyone except the admin who hid the mod.
        if exists is not None and exists.public:
            parsed_mod_id = candidate

    now = datetime.now(timezone.utc)
    thread = ForumThread(
        slug=slugify(title),
        title=title,
        body_html=render(body),
        body_raw=body,
        mod_id=parsed_mod_id,
        kind=kind,
        status="open",
        author_name=author_name,
        author_token=token,
        author_user_id=user.id if user else None,
        vote_score=0,
        reply_count=0,
        pinned=False,
        locked=False,
        created_at=now,
        updated_at=now,
        last_post_at=now,
    )
    session.add(thread)
    await session.flush()
    await _notify_mentions(
        session, body, thread.id, None, author_name,
        exclude_user_id=user.id if user else None,
    )
    await session.commit()
    await session.refresh(thread)
    return RedirectResponse(f"/forum/{thread.id}/{thread.slug}", status_code=303)


# ---------- view + reply ---------------------------------------------------

@router.get("/{thread_id:int}/{slug}", response_class=HTMLResponse)
@router.get("/{thread_id:int}", response_class=HTMLResponse)
async def forum_view(
    request: Request,
    response: Response,
    thread_id: int,
    slug: str = "",
    session: AsyncSession = Depends(get_db),
):
    token = get_or_create_token(request, response)
    thread = (
        await session.execute(
            select(ForumThread)
            .options(selectinload(ForumThread.mod))
            .where(ForumThread.id == thread_id)
        )
    ).scalar_one_or_none()
    if thread is None:
        raise HTTPException(404)
    posts = (
        await session.execute(
            select(ForumPost).where(ForumPost.thread_id == thread_id).order_by(ForumPost.created_at.asc())
        )
    ).scalars().all()
    user = await current_user(request, session)
    current_uid = user.id if user else None

    has_voted = False
    voter_weight = 1
    if current_uid is not None:
        has_voted = (
            await session.execute(
                select(ForumUpvote).where(
                    ForumUpvote.thread_id == thread_id,
                    ForumUpvote.voter_user_id == current_uid,
                )
            )
        ).scalar_one_or_none() is not None
        voter_weight = await vote_weight_for(session, current_uid)

    # Reactions: build {(post_id|None for thread): {emoji: count}} +
    # the current token's own reactions so we can highlight them.
    reaction_rows = (await session.execute(
        select(ForumReaction).where(
            (ForumReaction.thread_id == thread_id)
            | (ForumReaction.post_id.in_([p.id for p in posts] or [-1]))
        )
    )).scalars().all()
    reactions_by_target: dict = {}
    mine_by_target: dict = {}
    for r in reaction_rows:
        key = ("post", r.post_id) if r.post_id else ("thread", r.thread_id)
        bucket = reactions_by_target.setdefault(key, {})
        bucket[r.emoji] = bucket.get(r.emoji, 0) + 1
        if r.voter_token == token:
            mine_by_target.setdefault(key, set()).add(r.emoji)

    # Preload membership emoji for all authors — avoids N+1 queries.
    from app.models.membership import UserMembership, MembershipTier as _MT
    _author_ids: set[int] = set()
    if thread.author_user_id:
        _author_ids.add(thread.author_user_id)
    for p in posts:
        if p.author_user_id:
            _author_ids.add(p.author_user_id)
    member_emojis: dict[int, str] = {}
    if _author_ids:
        _mnow = datetime.now(timezone.utc)
        _mrows = (await session.execute(
            select(UserMembership)
            .join(_MT, UserMembership.tier_id == _MT.id)
            .where(
                UserMembership.user_id.in_(_author_ids),
                UserMembership.expires_at > _mnow,
            )
            .order_by(UserMembership.expires_at.desc())
        )).scalars().all()
        _seen: set[int] = set()
        for _m in _mrows:
            if _m.user_id not in _seen:
                await session.refresh(_m, ["tier"])
                member_emojis[_m.user_id] = _m.tier.emoji
                _seen.add(_m.user_id)

    return templates.TemplateResponse(
        request, "forum_thread.html",
        {
            "thread": thread,
            "posts": posts,
            "has_voted": has_voted,
            "voter_weight": voter_weight,
            "member_emojis": member_emojis,
            "is_owner": thread.author_token == token
                        or (current_uid and thread.author_user_id == current_uid),
            "is_admin": _is_admin(request),
            "current_user_id": current_uid,
            "current_token": token,
            "kinds": THREAD_KINDS,
            "statuses": THREAD_STATUSES,
            "edit_window_minutes": int(EDIT_WINDOW.total_seconds() / 60),
            "reactions_by_target": reactions_by_target,
            "mine_by_target": mine_by_target,
            "reaction_emojis": REACTION_EMOJIS,
        },
    )


@router.post("/{thread_id}/reply")
async def forum_reply(
    request: Request,
    response: Response,
    thread_id: int,
    body: str = Form(...),
    author_name: str = Form(...),
    website: str = Form(""),
    session: AsyncSession = Depends(get_db),
):
    token = get_or_create_token(request, response)
    thread = await session.get(ForumThread, thread_id)
    if thread is None:
        raise HTTPException(404)
    if thread.locked:
        raise HTTPException(403, detail="Thread is locked")

    body = body.strip()[:MAX_BODY_LEN]
    user = await current_user(request, session)
    if _is_admin(request):
        author_name = DEVELOPER_LABEL
    elif user is not None:
        # Never fall back to the email local-part for the public byline
        # — that would publish PII (even partial) in JSON-LD, RSS, etc.
        author_name = (user.name or f"user{user.id}")[:64]
    else:
        author_name = author_name.strip()[:64]
    if not _is_admin(request) and author_name.strip().lower() == DEVELOPER_LABEL.lower():
        author_name = "anon"
    reason = is_likely_spam("ok-reply-no-title-check", body, author_name, website)
    if reason:
        raise HTTPException(400, detail=reason)
    await _check_rate_limit(session, token, ForumPost, RATE_LIMIT_REPLIES_PER_HOUR)

    now = datetime.now(timezone.utc)
    post = ForumPost(
        thread_id=thread_id,
        body_html=render(body),
        body_raw=body,
        author_name=author_name,
        author_token=token,
        author_user_id=user.id if user else None,
        created_at=now,
    )
    session.add(post)
    thread.reply_count += 1
    thread.last_post_at = now
    thread.updated_at = now
    await session.flush()  # gets post.id before notification rows reference it

    # Notify the thread's author if they (a) have an account and (b) aren't
    # the one replying. We don't notify anon thread authors — they have no
    # inbox to read.
    if thread.author_user_id and (not user or thread.author_user_id != user.id):
        session.add(Notification(
            user_id=thread.author_user_id,
            kind="thread_reply",
            thread_id=thread.id,
            post_id=post.id,
            actor_name=author_name,
            payload=body[:200],
            created_at=now,
        ))

    # Also notify everyone else who previously replied in the thread (so a
    # conversation pings all participants, GitHub-issue style). Dedupe so
    # the same person doesn't get N copies. Capped at MAX_FANOUT_PER_POST
    # so a thread with thousands of repliers can't be weaponized into a
    # notification flood on every new reply.
    prev_repliers = (
        await session.execute(
            select(ForumPost.author_user_id)
            .where(
                ForumPost.thread_id == thread_id,
                ForumPost.author_user_id.is_not(None),
                ForumPost.id != post.id,
            )
            .distinct()
            .limit(MAX_FANOUT_PER_POST)
        )
    ).scalars().all()
    already_notified = {thread.author_user_id, user.id if user else None}
    for replier_id in prev_repliers:
        if replier_id in already_notified:
            continue
        session.add(Notification(
            user_id=replier_id,
            kind="reply_to_reply",
            thread_id=thread.id,
            post_id=post.id,
            actor_name=author_name,
            payload=body[:200],
            created_at=now,
        ))
        already_notified.add(replier_id)

    await _notify_mentions(
        session, body, thread.id, post.id, author_name,
        exclude_user_id=user.id if user else None,
    )

    await session.commit()
    return RedirectResponse(f"/forum/{thread_id}/{thread.slug}#post-{post.id}", status_code=303)


# ---------- upvote toggle --------------------------------------------------

@router.post("/{thread_id}/upvote")
async def forum_upvote(
    request: Request,
    thread_id: int,
    session: AsyncSession = Depends(get_db),
):
    user_state = request.state.user
    if user_state is None:
        from fastapi.responses import JSONResponse as _JSONResponse
        return _JSONResponse({"error": "login_required"}, status_code=401)

    user_id = user_state["id"]
    thread = await session.get(ForumThread, thread_id)
    if thread is None:
        raise HTTPException(404)

    existing = (
        await session.execute(
            select(ForumUpvote).where(
                ForumUpvote.thread_id == thread_id,
                ForumUpvote.voter_user_id == user_id,
            )
        )
    ).scalar_one_or_none()

    from sqlalchemy import update as _update
    if existing is not None:
        snapped = existing.vote_weight
        await session.delete(existing)
        await session.execute(
            _update(ForumThread)
            .where(ForumThread.id == thread_id, ForumThread.vote_score >= snapped)
            .values(vote_score=ForumThread.vote_score - snapped)
        )
    else:
        weight = await vote_weight_for(session, user_id)
        session.add(ForumUpvote(
            thread_id=thread_id,
            voter_token="",  # sentinel: authenticated votes use voter_user_id, not token
            voter_user_id=user_id,
            vote_weight=weight,
            created_at=datetime.now(timezone.utc),
        ))
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            return RedirectResponse(f"/forum/{thread_id}/{thread.slug}", status_code=303)
        await session.execute(
            _update(ForumThread)
            .where(ForumThread.id == thread_id)
            .values(vote_score=ForumThread.vote_score + weight)
        )
    await session.commit()
    return RedirectResponse(f"/forum/{thread_id}/{thread.slug}", status_code=303)


# ---------- reactions (👍 ❤️ 🎉 etc) -------------------------------------

@router.post("/react")
async def forum_react(
    request: Request,
    response: Response,
    emoji: str = Form(...),
    thread_id: int = Form(None),
    post_id: int = Form(None),
    session: AsyncSession = Depends(get_db),
):
    """Toggle a reaction. Same cookie + same emoji on same post = remove."""
    if emoji not in REACTION_EMOJIS:
        raise HTTPException(400, detail="bad emoji")
    if not thread_id and not post_id:
        raise HTTPException(400, detail="thread_id or post_id required")

    token = get_or_create_token(request, response)
    # Reactions were previously unrate-limited — a single voter could
    # POST millions of toggle requests and (for thread-level reactions,
    # whose unique constraint NULL-skipped before the partial-unique
    # migration) flood the DB. Cap at RATE_LIMIT_REACTIONS_PER_HOUR / token.
    await _check_rate_limit(session, token, ForumReaction, RATE_LIMIT_REACTIONS_PER_HOUR)

    where = ForumReaction.voter_token == token
    where &= ForumReaction.emoji == emoji
    if post_id:
        where &= ForumReaction.post_id == post_id
    else:
        where &= ForumReaction.thread_id == thread_id
        where &= ForumReaction.post_id.is_(None)

    existing = (await session.execute(select(ForumReaction).where(where))).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
    else:
        session.add(ForumReaction(
            thread_id=thread_id if not post_id else None,
            post_id=post_id,
            voter_token=token,
            emoji=emoji,
            created_at=datetime.now(timezone.utc),
        ))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()

    # Same-origin only — never trust the Referer header as a redirect
    # target (open-redirect → phishing).
    target = _safe_referer(request, fallback="/forum")
    return RedirectResponse(target + (f"#post-{post_id}" if post_id else ""), status_code=303)


# ---------- delete own post within window ----------------------------------

EDIT_WINDOW = timedelta(minutes=30)


def _can_edit(request: Request, author_token: str, author_user_id: int | None,
              current_user_id: int | None, created_at: datetime) -> bool:
    """Edit allowed if (a) you're admin, or (b) you're the author (cookie
    or account match) AND within EDIT_WINDOW of creation."""
    if _check_admin(request):
        return True
    if (datetime.now(timezone.utc) - created_at) > EDIT_WINDOW:
        return False
    if current_user_id is not None and author_user_id == current_user_id:
        return True
    cookie_token = get_token(request)
    return cookie_token is not None and cookie_token == author_token


@router.get("/post/{post_id}/edit", response_class=HTMLResponse)
async def forum_post_edit_form(
    request: Request,
    post_id: int,
    session: AsyncSession = Depends(get_db),
):
    post = await session.get(ForumPost, post_id)
    if post is None:
        raise HTTPException(404)
    user = await current_user(request, session)
    if not _can_edit(request, post.author_token, post.author_user_id,
                     user.id if user else None, post.created_at):
        raise HTTPException(403, detail="Can't edit this post")
    return templates.TemplateResponse(
        request, "forum_edit_post.html",
        {"post": post, "kind": "reply"},
    )


@router.post("/post/{post_id}/edit")
async def forum_post_edit_submit(
    request: Request,
    post_id: int,
    body: str = Form(...),
    session: AsyncSession = Depends(get_db),
):
    post = await session.get(ForumPost, post_id)
    if post is None:
        raise HTTPException(404)
    user = await current_user(request, session)
    if not _can_edit(request, post.author_token, post.author_user_id,
                     user.id if user else None, post.created_at):
        raise HTTPException(403)
    # Block edits if the parent thread is locked — otherwise a user
    # within their 30-min edit window can defeat moderation by
    # rewriting their post after an admin locks the thread.
    # Admins still bypass via _can_edit returning True.
    thread = await session.get(ForumThread, post.thread_id)
    if thread is not None and thread.locked and not _check_admin(request):
        raise HTTPException(403, detail="Thread is locked")
    body = body.strip()[:MAX_BODY_LEN]
    if len(body) < 10:
        raise HTTPException(400, detail="body too short")
    post.body_raw = body
    post.body_html = render(body)
    post.edited_at = datetime.now(timezone.utc)
    await session.commit()
    slug = thread.slug if thread else ""
    return RedirectResponse(f"/forum/{post.thread_id}/{slug}#post-{post.id}", status_code=303)


@router.get("/{thread_id}/edit", response_class=HTMLResponse)
async def forum_thread_edit_form(
    request: Request,
    thread_id: int,
    session: AsyncSession = Depends(get_db),
):
    thread = await session.get(ForumThread, thread_id)
    if thread is None:
        raise HTTPException(404)
    user = await current_user(request, session)
    if not _can_edit(request, thread.author_token, thread.author_user_id,
                     user.id if user else None, thread.created_at):
        raise HTTPException(403, detail="Can't edit this thread")
    return templates.TemplateResponse(
        request, "forum_edit_post.html",
        {"thread": thread, "kind": "thread"},
    )


@router.post("/{thread_id}/edit")
async def forum_thread_edit_submit(
    request: Request,
    thread_id: int,
    title: str = Form(...),
    body: str = Form(...),
    session: AsyncSession = Depends(get_db),
):
    thread = await session.get(ForumThread, thread_id)
    if thread is None:
        raise HTTPException(404)
    user = await current_user(request, session)
    if not _can_edit(request, thread.author_token, thread.author_user_id,
                     user.id if user else None, thread.created_at):
        raise HTTPException(403)
    if thread.locked and not _check_admin(request):
        raise HTTPException(403, detail="Thread is locked")
    title = title.strip()[:MAX_TITLE_LEN]
    body = body.strip()[:MAX_BODY_LEN]
    if len(title) < 5 or len(body) < 10:
        raise HTTPException(400, detail="title/body too short")
    thread.title = title
    thread.body_raw = body
    thread.body_html = render(body)
    thread.edited_at = datetime.now(timezone.utc)
    thread.updated_at = thread.edited_at
    await session.commit()
    return RedirectResponse(f"/forum/{thread.id}/{thread.slug}", status_code=303)


@router.post("/post/{post_id}/delete")
async def forum_post_delete(
    request: Request,
    post_id: int,
    session: AsyncSession = Depends(get_db),
):
    token = get_token(request)
    post = await session.get(ForumPost, post_id)
    if post is None:
        raise HTTPException(404)
    is_admin = _is_admin(request)
    user = await current_user(request, session)
    # Ownership = cookie-token match OR (logged in AND user_id match).
    # Mirrors _can_edit so a logged-in user who cleared mb_token can
    # still delete their own post within the window — previously they
    # were locked out because we only checked the token.
    in_window = (datetime.now(timezone.utc) - post.created_at) <= POST_WINDOW
    is_owner_in_window = in_window and (
        (token is not None and token == post.author_token)
        or (user is not None and post.author_user_id == user.id)
    )
    if not (is_admin or is_owner_in_window):
        raise HTTPException(403)

    thread = await session.get(ForumThread, post.thread_id)
    await session.delete(post)
    if thread is not None:
        thread.reply_count = max(0, thread.reply_count - 1)
    await session.commit()
    return RedirectResponse(f"/forum/{post.thread_id}", status_code=303)


@router.post("/{thread_id}/delete")
async def forum_thread_delete(
    request: Request,
    thread_id: int,
    session: AsyncSession = Depends(get_db),
):
    token = get_token(request)
    thread = await session.get(ForumThread, thread_id)
    if thread is None:
        raise HTTPException(404)
    is_admin = _is_admin(request)
    is_owner_in_window = (
        token == thread.author_token
        and (datetime.now(timezone.utc) - thread.created_at) <= POST_WINDOW
    )
    if not (is_admin or is_owner_in_window):
        raise HTTPException(403)
    await session.delete(thread)
    await session.commit()
    return RedirectResponse("/forum", status_code=303)


# ---------- admin actions (Basic auth) -------------------------------------

@router.post("/{thread_id}/status", dependencies=[Depends(require_admin_or_403)])
async def forum_set_status(
    thread_id: int,
    status: str = Form(...),
    session: AsyncSession = Depends(get_db),
):
    if status not in THREAD_STATUSES:
        raise HTTPException(400, detail="bad status")
    thread = await session.get(ForumThread, thread_id)
    if thread is None:
        raise HTTPException(404)
    old_status = thread.status
    thread.status = status
    thread.updated_at = datetime.now(timezone.utc)
    if thread.author_user_id and old_status != status:
        session.add(Notification(
            user_id=thread.author_user_id,
            kind="thread_status",
            thread_id=thread.id,
            actor_name=DEVELOPER_LABEL,
            payload=status,
            created_at=thread.updated_at,
        ))
    await session.commit()
    return RedirectResponse(f"/forum/{thread_id}/{thread.slug}", status_code=303)


@router.post("/{thread_id}/pin", dependencies=[Depends(require_admin_or_403)])
async def forum_toggle_pin(
    thread_id: int,
    session: AsyncSession = Depends(get_db),
):
    thread = await session.get(ForumThread, thread_id)
    if thread is None:
        raise HTTPException(404)
    thread.pinned = not thread.pinned
    await session.commit()
    return RedirectResponse(f"/forum/{thread_id}/{thread.slug}", status_code=303)


@router.post("/{thread_id}/lock", dependencies=[Depends(require_admin_or_403)])
async def forum_toggle_lock(
    thread_id: int,
    session: AsyncSession = Depends(get_db),
):
    thread = await session.get(ForumThread, thread_id)
    if thread is None:
        raise HTTPException(404)
    was_locked = thread.locked
    thread.locked = not thread.locked
    if thread.locked and not was_locked and thread.author_user_id:
        session.add(Notification(
            user_id=thread.author_user_id,
            kind="thread_locked",
            thread_id=thread.id,
            actor_name=DEVELOPER_LABEL,
            created_at=datetime.now(timezone.utc),
        ))
    await session.commit()
    return RedirectResponse(f"/forum/{thread_id}/{thread.slug}", status_code=303)
