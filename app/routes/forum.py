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
from app.models import (
    THREAD_KINDS,
    THREAD_STATUSES,
    ForumPost,
    ForumThread,
    ForumUpvote,
    Mod,
)
from app.services.anon import get_or_create_token, get_token
from app.services.auth import admin_marker_present, require_admin
from app.services.textfmt import is_likely_spam, render, slugify

DEVELOPER_LABEL = "Developer"

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
    return templates.TemplateResponse(
        request, "forum_list.html",
        {
            "threads": threads,
            "counts": counts,
            "active_kind": kind,
            "active_status": status,
            "kinds": THREAD_KINDS,
            "statuses": THREAD_STATUSES,
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
    title = title.strip()[:256]
    body = body.strip()
    kind = kind if kind in THREAD_KINDS else "discussion"
    # Admins post under a fixed "Developer" label instead of typing a name.
    if _is_admin(request):
        author_name = DEVELOPER_LABEL
    else:
        author_name = author_name.strip()[:64]

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
        if exists is not None:
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
        upvotes=0,
        reply_count=0,
        pinned=False,
        locked=False,
        created_at=now,
        updated_at=now,
        last_post_at=now,
    )
    session.add(thread)
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
    has_voted = (
        await session.execute(
            select(ForumUpvote).where(
                ForumUpvote.thread_id == thread_id,
                ForumUpvote.voter_token == token,
            )
        )
    ).scalar_one_or_none() is not None
    return templates.TemplateResponse(
        request, "forum_thread.html",
        {
            "thread": thread,
            "posts": posts,
            "has_voted": has_voted,
            "is_owner": thread.author_token == token,
            "is_admin": _is_admin(request),
            "kinds": THREAD_KINDS,
            "statuses": THREAD_STATUSES,
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

    body = body.strip()
    if _is_admin(request):
        author_name = DEVELOPER_LABEL
    else:
        author_name = author_name.strip()[:64]
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
        created_at=now,
    )
    session.add(post)
    thread.reply_count += 1
    thread.last_post_at = now
    thread.updated_at = now
    await session.commit()
    return RedirectResponse(f"/forum/{thread_id}/{thread.slug}#post-{post.id}", status_code=303)


# ---------- upvote toggle --------------------------------------------------

@router.post("/{thread_id}/upvote")
async def forum_upvote(
    request: Request,
    response: Response,
    thread_id: int,
    session: AsyncSession = Depends(get_db),
):
    token = get_or_create_token(request, response)
    thread = await session.get(ForumThread, thread_id)
    if thread is None:
        raise HTTPException(404)

    existing = (
        await session.execute(
            select(ForumUpvote).where(
                ForumUpvote.thread_id == thread_id,
                ForumUpvote.voter_token == token,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        await session.delete(existing)
        thread.upvotes = max(0, thread.upvotes - 1)
    else:
        session.add(ForumUpvote(
            thread_id=thread_id,
            voter_token=token,
            created_at=datetime.now(timezone.utc),
        ))
        thread.upvotes += 1
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
    return RedirectResponse(f"/forum/{thread_id}/{thread.slug}", status_code=303)


# ---------- delete own post within window ----------------------------------

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
    is_owner_in_window = (
        token == post.author_token
        and (datetime.now(timezone.utc) - post.created_at) <= POST_WINDOW
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

@router.post("/{thread_id}/status", dependencies=[Depends(require_admin)])
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
    thread.status = status
    thread.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return RedirectResponse(f"/forum/{thread_id}/{thread.slug}", status_code=303)


@router.post("/{thread_id}/pin", dependencies=[Depends(require_admin)])
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


@router.post("/{thread_id}/lock", dependencies=[Depends(require_admin)])
async def forum_toggle_lock(
    thread_id: int,
    session: AsyncSession = Depends(get_db),
):
    thread = await session.get(ForumThread, thread_id)
    if thread is None:
        raise HTTPException(404)
    thread.locked = not thread.locked
    await session.commit()
    return RedirectResponse(f"/forum/{thread_id}/{thread.slug}", status_code=303)
