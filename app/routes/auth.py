"""Auth routes — unified /auth/login (Google + admin form), profile, logout."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import get_db
from app.models import ForumPost, ForumThread, User
from app.services.auth import (
    clear_admin_session,
    is_admin,
    set_admin_session,
    verify_admin_credentials,
)
from app.services.oauth_google import (
    OAUTH_NEXT_COOKIE,
    OAUTH_STATE_COOKIE,
    authorize_url,
    exchange_code,
    fetch_userinfo,
    make_state,
)
from app.services.session import (
    SESSION_MAX_AGE,
    clear_session,
    current_user,
    session_user_id,
    set_session,
)

router = APIRouter(prefix="/auth")
templates = Jinja2Templates(directory="app/templates")


def _safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


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
    if not verify_admin_credentials(username.strip(), password):
        return RedirectResponse(
            f"/auth/login?error=Invalid+admin+credentials&next={_safe_next(next)}",
            status_code=303,
        )
    response = RedirectResponse(_safe_next(next), status_code=303)
    set_admin_session(response)
    return response


@router.post("/admin/logout")
@router.get("/admin/logout")
async def admin_logout():
    response = RedirectResponse("/", status_code=303)
    clear_admin_session(response)
    return response


@router.get("/google/login")
async def google_login(request: Request, next: str = "/"):
    if not settings.google_oauth_enabled:
        raise HTTPException(503, detail="Google login is not configured")
    state = make_state()
    response = RedirectResponse(authorize_url(state), status_code=302)
    response.set_cookie(
        OAUTH_STATE_COOKIE, state, max_age=600,
        httponly=True, samesite="lax", secure=False,
    )
    # Preserve where to land after login (relative URL only — never trust
    # external redirects)
    if next.startswith("/") and not next.startswith("//"):
        response.set_cookie(
            OAUTH_NEXT_COOKIE, next, max_age=600,
            httponly=True, samesite="lax", secure=False,
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

    try:
        token_resp = await exchange_code(code)
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
    if not sub or not email:
        raise HTTPException(502, detail="Missing sub/email in userinfo")

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
    else:
        # Refresh profile fields in case the user updated them on Google's side
        user.email = email
        user.name = info.get("name") or user.name
        user.avatar_url = info.get("picture") or user.avatar_url
        user.last_login_at = now
    await session.commit()
    await session.refresh(user)

    next_path = request.cookies.get(OAUTH_NEXT_COOKIE) or "/"
    if not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/"

    response = RedirectResponse(next_path, status_code=303)
    set_session(response, user.id)
    response.delete_cookie(OAUTH_STATE_COOKIE, samesite="lax")
    response.delete_cookie(OAUTH_NEXT_COOKIE, samesite="lax")
    return response


@router.get("/logout")
@router.post("/logout")
async def logout():
    """Single Logout endpoint that clears both the user session cookie
    and the admin session cookie. Used by the nav Logout link regardless
    of which identity(ies) are signed in."""
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
