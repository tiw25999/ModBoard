"""Auth routes — unified /auth/login (Google + admin form), profile, logout."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import get_db
from app.models import ForumPost, ForumThread, Notification, User
from app.services.auth import (
    clear_admin_session,
    is_admin,
    set_admin_session,
    verify_admin_credentials,
)
from app.services.oauth_google import (
    OAUTH_NEXT_COOKIE,
    OAUTH_PKCE_COOKIE,
    OAUTH_STATE_COOKIE,
    authorize_url,
    exchange_code,
    fetch_userinfo,
    make_pkce_verifier,
    make_state,
    pkce_challenge,
)
from app.services.ratelimit import check_and_record, client_ip, reset as _rl_reset
from app.services.session import (
    SESSION_MAX_AGE,
    clear_session,
    current_user,
    parse_session,
    set_session,
)

router = APIRouter(prefix="/auth")
templates = Jinja2Templates(directory="app/templates")


def _safe_next(value: str | None) -> str:
    r"""Allow only relative same-origin paths. Rejects:
      - empty / non-string
      - protocol-relative (//evil.com)
      - backslash variants browsers may normalize to / (/\evil.com)
      - percent-encoded slashes that decode to // or \\ after parsing
    """
    if not value or not value.startswith("/"):
        return "/"
    head = value[:8].lower()
    if value.startswith("//") or "\\" in head:
        return "/"
    if "%2f" in head or "%5c" in head:
        return "/"
    return value


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: str = "/",
    error: str | None = None,
):
    return templates.TemplateResponse(
        request, "login.html",
        {
            "next": _safe_next(next),
            "error": error,
            "google_enabled": settings.google_oauth_enabled,
        },
    )


@router.post("/admin/login")
async def admin_login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/admin"),
):
    # Per-IP token bucket — 5 attempts / 15 min. Defence in depth
    # behind Cloudflare's per-minute rule for when CF gets bypassed.
    ip = client_ip(request)
    if not check_and_record(f"admin_login:{ip}"):
        return RedirectResponse(
            f"/auth/login?error=Too+many+attempts.+Try+again+in+15+min.&next={_safe_next(next)}",
            status_code=303,
        )
    if not verify_admin_credentials(username.strip(), password):
        return RedirectResponse(
            f"/auth/login?error=Invalid+admin+credentials&next={_safe_next(next)}",
            status_code=303,
        )
    # Successful login → clear the failure counter so a future typo
    # doesn't lock the admin out.
    _rl_reset(f"admin_login:{ip}")
    response = RedirectResponse(_safe_next(next), status_code=303)
    set_admin_session(response)
    return response


@router.post("/admin/logout")
async def admin_logout():
    # POST-only — a GET would let `<img src="/auth/admin/logout">` on a
    # malicious page log the admin out silently (logout-CSRF). The nav
    # already posts to /auth/logout which clears both cookies in one go.
    response = RedirectResponse("/", status_code=303)
    clear_admin_session(response)
    return response


@router.get("/google/login")
async def google_login(request: Request, next: str = "/"):
    if not settings.google_oauth_enabled:
        raise HTTPException(503, detail="Google login is not configured")
    state = make_state()
    verifier = make_pkce_verifier()
    challenge = pkce_challenge(verifier)
    response = RedirectResponse(authorize_url(state, code_challenge=challenge), status_code=302)
    response.set_cookie(
        OAUTH_STATE_COOKIE, state, max_age=600,
        httponly=True, samesite="lax", secure=settings.secure_cookies,
    )
    # PKCE verifier lives in a short-lived HttpOnly cookie. Callback
    # reads it and sends it to Google's token endpoint, which checks
    # SHA256(verifier) == challenge. Stops an attacker who intercepts
    # the auth code (browser extension, redirect leak) from
    # exchanging it without also stealing this cookie.
    response.set_cookie(
        OAUTH_PKCE_COOKIE, verifier, max_age=600,
        httponly=True, samesite="lax", secure=settings.secure_cookies,
    )
    # Preserve where to land after login (relative URL only — never trust
    # external redirects). _safe_next handles backslash + percent-encoded
    # bypass variants too.
    safe = _safe_next(next)
    if safe != "/":
        response.set_cookie(
            OAUTH_NEXT_COOKIE, safe, max_age=600,
            httponly=True, samesite="lax", secure=settings.secure_cookies,
        )
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: AsyncSession = Depends(get_db),
):
    if error:
        raise HTTPException(400, detail=f"Google rejected the request: {error}")
    if not code or not state:
        raise HTTPException(400, detail="Missing code or state")

    expected_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not expected_state or expected_state != state:
        raise HTTPException(400, detail="State mismatch — possible CSRF, try again")

    pkce_verifier = request.cookies.get(OAUTH_PKCE_COOKIE)
    if not pkce_verifier:
        raise HTTPException(400, detail="Missing PKCE verifier — start login again")

    try:
        token_resp = await exchange_code(code, code_verifier=pkce_verifier)
    except Exception as e:
        raise HTTPException(502, detail=f"Token exchange failed: {e}")
    access_token = token_resp.get("access_token")
    if not access_token:
        raise HTTPException(502, detail="No access_token in Google response")

    try:
        info = await fetch_userinfo(access_token)
    except Exception as e:
        raise HTTPException(502, detail=f"Userinfo fetch failed: {e}")

    sub = info.get("sub")
    email = info.get("email")
    email_verified = info.get("email_verified") is True
    if not sub or not email:
        raise HTTPException(502, detail="Missing sub/email in userinfo")
    # Without email_verified=true, anyone running their own Google Workspace
    # could mint an account claiming someone else's email. Reject — it's
    # an identity-spoofing vector even if we don't currently look up users
    # by email.
    if not email_verified:
        raise HTTPException(
            403,
            detail="Your Google account's email is not verified. "
                   "Verify it with Google and try again.",
        )

    now = datetime.now(timezone.utc)
    user = (
        await session.execute(select(User).where(User.google_sub == sub))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            google_sub=sub,
            email=email,
            name=info.get("name") or info.get("given_name") or email.split("@")[0],
            avatar_url=info.get("picture"),
            created_at=now,
            last_login_at=now,
        )
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            # Two simultaneous first-logins for the same Google account
            # both missed the SELECT above. The unique constraint on
            # google_sub catches one — pick up the winner's row instead
            # of returning 500.
            await session.rollback()
            user = (
                await session.execute(select(User).where(User.google_sub == sub))
            ).scalar_one()
            user.last_login_at = now
            await session.commit()
    else:
        # Refresh profile fields — but only the email if Google still
        # reports it as verified AND it actually changed. Avatar/name
        # are display-only so safe to refresh unconditionally.
        if email_verified and user.email != email:
            user.email = email
        user.name = info.get("name") or user.name
        user.avatar_url = info.get("picture") or user.avatar_url
        user.last_login_at = now
        await session.commit()
    await session.refresh(user)

    next_path = request.cookies.get(OAUTH_NEXT_COOKIE) or "/"
    if not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/"

    # Rotate the per-user session token on every successful login
    # (defends against session fixation: a pre-planted cookie value
    # becomes invalid the moment its target logs in).
    user.session_jti = secrets.token_urlsafe(16)
    await session.commit()

    response = RedirectResponse(next_path, status_code=303)
    set_session(response, user.id, user.session_jti)
    response.delete_cookie(OAUTH_STATE_COOKIE, samesite="lax")
    response.delete_cookie(OAUTH_NEXT_COOKIE, samesite="lax")
    response.delete_cookie(OAUTH_PKCE_COOKIE, samesite="lax")
    return response


@router.post("/logout")
async def logout(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Logout — clears both the user session cookie and the admin
    session cookie, AND nulls out user.session_jti so an exfiltrated
    cookie copy stops working server-side (client deletion alone
    cannot revoke what an attacker already stole)."""
    parsed = parse_session(request)
    if parsed is not None:
        uid, _ = parsed
        user = await session.get(User, uid)
        if user is not None:
            user.session_jti = None
            await session.commit()
    response = RedirectResponse("/", status_code=303)
    clear_session(response)
    clear_admin_session(response)
    return response


@router.get("/me", response_class=HTMLResponse)
async def me(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    user = await current_user(request, session)
    if user is None:
        return RedirectResponse("/auth/google/login?next=/auth/me", status_code=303)

    my_threads = (
        await session.execute(
            select(ForumThread)
            .options(selectinload(ForumThread.mod))
            .where(ForumThread.author_user_id == user.id)
            .order_by(desc(ForumThread.created_at))
            .limit(50)
        )
    ).scalars().all()
    my_replies = (
        await session.execute(
            select(ForumPost)
            .where(ForumPost.author_user_id == user.id)
            .order_by(desc(ForumPost.created_at))
            .limit(50)
        )
    ).scalars().all()

    return templates.TemplateResponse(
        request, "profile.html",
        {"user": user, "my_threads": my_threads, "my_replies": my_replies},
    )


@router.get("/me/notifications", response_class=HTMLResponse)
async def my_notifications(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Inbox page. Marks everything read on view — typical "go look at it"
    semantics. (Could split into "unread / all" tabs later if needed.)"""
    user = await current_user(request, session)
    if user is None:
        return RedirectResponse("/auth/login?next=/auth/me/notifications", status_code=303)

    notes = (
        await session.execute(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(desc(Notification.created_at))
            .limit(100)
        )
    ).scalars().all()

    thread_ids = {n.thread_id for n in notes if n.thread_id}
    threads = {}
    if thread_ids:
        rows = (await session.execute(
            select(ForumThread).where(ForumThread.id.in_(thread_ids))
        )).scalars().all()
        threads = {t.id: t for t in rows}
    from app.models import Mod
    mod_ids = {n.mod_id for n in notes if n.mod_id}
    mods_lookup = {}
    if mod_ids:
        rows = (await session.execute(select(Mod).where(Mod.id.in_(mod_ids)))).scalars().all()
        mods_lookup = {m.id: m for m in rows}

    # Mark unread as read on view
    now = datetime.now(timezone.utc)
    for n in notes:
        if n.read_at is None:
            n.read_at = now
    if notes:
        await session.commit()

    return templates.TemplateResponse(
        request, "notifications.html",
        {"user": user, "notes": notes, "threads": threads, "mods_lookup": mods_lookup},
    )


@router.post("/me/notifications/clear")
async def clear_notifications(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    user = await current_user(request, session)
    if user is None:
        return RedirectResponse("/auth/login", status_code=303)
    from sqlalchemy import delete as _delete
    await session.execute(_delete(Notification).where(Notification.user_id == user.id))
    await session.commit()
    return RedirectResponse("/auth/me/notifications", status_code=303)
