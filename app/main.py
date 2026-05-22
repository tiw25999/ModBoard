"""ModBoard FastAPI app."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db import SessionLocal
from app.routes import admin as admin_routes
from app.routes import auth as auth_routes
from app.routes import forum as forum_routes
from app.routes import public as public_routes
from app.routes import feeds as feeds_routes
from app.routes import seo as seo_routes
from app.services.auth import is_admin as _is_admin_cookie
from app.services.poller import poller_task
from app.services.session import session_user_id

_error_templates = Jinja2Templates(directory="app/templates")

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # With multiple app replicas behind the load balancer, only one should
    # run the Steam poller (otherwise we double the API rate + risk duplicate
    # notifications). Set RUN_POLLER=false on the standby replica.
    task = None
    if os.getenv("RUN_POLLER", "true").lower() == "true":
        task = asyncio.create_task(poller_task())
    yield
    if task is not None:
        task.cancel()


app = FastAPI(title="ModBoard", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(seo_routes.router)   # robots.txt + sitemap.xml at root
app.include_router(feeds_routes.router) # /forum.rss, /news.rss, /mod/{id}/comments.rss, etc.
app.include_router(public_routes.router)
app.include_router(forum_routes.router)
app.include_router(auth_routes.router)
app.include_router(admin_routes.router)


# ---- Security headers ---------------------------------------------------
# Set on every response. CSP is moderately strict (img-src https: allows
# Steam + Google avatars; 'unsafe-inline' is required for our inline
# JSON-LD blocks + the SW registration <script> in base.html). frame-
# ancestors 'none' kills clickjacking; X-Content-Type-Options stops MIME
# sniffing; Referrer-Policy trims leakage; Permissions-Policy disables
# sensor APIs we never use.
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "img-src 'self' https: data:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "upgrade-insecure-requests"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for k, v in _SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    # HSTS only on real production (cookies-Secure flag tracks the same
    # thing). Cloudflare will also set this, but defense in depth is free.
    from app.config import settings as _s
    if _s.production:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=63072000; includeSubDomains; preload",
        )
    return response


@app.middleware("http")
async def admin_gate(request: Request, call_next):
    """Anything under /admin/* requires the signed mb_admin cookie set by
    the /auth/admin/login form. No cookie → bounce to the login page."""
    path = request.url.path
    if path == "/admin" or path.startswith("/admin/"):
        if not _is_admin_cookie(request):
            return RedirectResponse(f"/auth/login?next={path}", status_code=303)
    return await call_next(request)


@app.middleware("http")
async def attach_current_user(request: Request, call_next):
    """Look up the logged-in user + admin status + unread notification
    count once per request so templates can read request.state.* without
    per-template DB round-trips."""
    request.state.user = None
    request.state.unread_count = 0
    request.state.is_admin = _is_admin_cookie(request)
    request.state.banner = None
    # Skip the per-request DB round-trip for static assets and the
    # health probe — both run every few seconds and never need user
    # context.
    path = request.url.path
    if path.startswith("/static") or path == "/health":
        return await call_next(request)
    uid = session_user_id(request)
    if uid is not None:
        from sqlalchemy import func, select as _select
        from app.models import Notification, User
        async with SessionLocal() as db:
            user = await db.get(User, uid)
            if user is not None:
                request.state.user = {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                    "avatar_url": user.avatar_url,
                }
                request.state.unread_count = int(
                    (await db.execute(
                        _select(func.count())
                        .select_from(Notification)
                        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
                    )).scalar() or 0
                )

    # Fetch the active banner news post (newest one flagged show_banner)
    # cheaply — once per request, cached for 60s site-wide.
    from app.services.cache import cached
    from sqlalchemy import select as _bsel
    from app.models import NewsPost as _News
    async def _fetch_banner():
        async with SessionLocal() as db:
            row = (await db.execute(
                _bsel(_News)
                .where(_News.active.is_(True), _News.show_banner.is_(True))
                .order_by(_News.created_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            if row is None:
                return None
            return {"id": row.id, "title": row.title, "kind": row.kind}
    request.state.banner = await cached("site:banner", 60, _fetch_banner)

    return await call_next(request)


@app.exception_handler(StarletteHTTPException)
async def html_exception_handler(request: Request, exc: StarletteHTTPException):
    """Render a themed error page for HTML clients; keep JSON for API/probe calls."""
    accept = request.headers.get("accept", "")
    if "text/html" in accept and exc.status_code >= 400 and exc.status_code != 401:
        # Make sure middleware-attached attrs exist (handler runs before per-route ones in some cases)
        if not hasattr(request.state, "is_admin"):
            request.state.is_admin = _is_admin_cookie(request)
        if not hasattr(request.state, "user"):
            request.state.user = None
        return _error_templates.TemplateResponse(
            request, "error.html",
            {"status_code": exc.status_code, "message": exc.detail},
            status_code=exc.status_code,
        )
    # 401 keeps the WWW-Authenticate header so OAuth/Basic still works
    from starlette.responses import JSONResponse
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
