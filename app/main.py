"""ModBoard FastAPI app."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings as _settings
from app.db import SessionLocal
from app.services.bbcode import safe_url
from app.services.csrf import csrf_middleware
from app.routes import admin as admin_routes
from app.routes import auth as auth_routes
from app.routes import forum as forum_routes
from app.routes import public as public_routes
from app.routes import feeds as feeds_routes
from app.routes import seo as seo_routes
from app.services.auth import is_admin as _is_admin_cookie
from app.services.poller import poller_task
from app.services.session import parse_session

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

# Fail-fast on weak operator config — better to refuse to boot than
# ship with a guessable SESSION_SECRET that lets anyone forge cookies.
if len(_settings.session_secret) < 32 or _settings.session_secret in (
    "change-me-to-a-long-random-string", "change-me", "secret", "",
):
    raise RuntimeError(
        "SESSION_SECRET is missing, too short, or still the placeholder. "
        "Set it to ≥32 random chars (e.g. `python -c 'import secrets; "
        "print(secrets.token_urlsafe(48))'`) before booting."
    )

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Expose `safe_url` as a Jinja filter so every template that renders a
# user-controlled href can pass it through:  href="{{ url|safe_url }}"
# Blocks javascript:/data:/vbscript: schemes at render time.
_error_templates.env.filters["safe_url"] = safe_url
for _r in (auth_routes, forum_routes, public_routes):
    if hasattr(_r, "templates"):
        _r.templates.env.filters["safe_url"] = safe_url
app.include_router(seo_routes.router)   # robots.txt + sitemap.xml at root
app.include_router(feeds_routes.router) # /forum.rss, /news.rss, /mod/{id}/comments.rss, etc.
app.include_router(public_routes.router)
app.include_router(forum_routes.router)
app.include_router(auth_routes.router)
app.include_router(admin_routes.router)


# ---- Security headers ---------------------------------------------------
# CSP uses a per-request nonce for inline <script> blocks (JSON-LD, SW
# registration) instead of 'unsafe-inline'. That means an injected
# <script> without the nonce — exactly what an XSS would inject —
# won't execute. style-src keeps 'unsafe-inline' because we use
# style="..." attributes in dozens of templates; dropping it would be
# a much larger refactor for a smaller security win.
_SECURITY_HEADERS_STATIC = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
}


def _csp_for(nonce: str) -> str:
    # cdn.jsdelivr.net is needed for the Chart.js bundle used on the
    # mod stats page. Everything else stays on 'self'.
    return (
        "default-src 'self'; "
        "img-src 'self' https: data:; "
        "style-src 'self' 'unsafe-inline'; "
        f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "upgrade-insecure-requests"
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    import secrets as _secrets
    nonce = _secrets.token_urlsafe(16)
    request.state.csp_nonce = nonce
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", _csp_for(nonce))
    for k, v in _SECURITY_HEADERS_STATIC.items():
        response.headers.setdefault(k, v)
    return response


# Hard cap on inbound request size — defends against DoS via huge
# POST bodies (a single 2GB form post would OOM the worker otherwise).
MAX_REQUEST_BYTES = 256 * 1024  # 256KB is plenty for forum + admin forms


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_REQUEST_BYTES:
        return JSONResponse(
            {"detail": "request body too large"},
            status_code=413,
        )
    return await call_next(request)


# CSRF must run AFTER body-size middleware (no point parsing CSRF on
# a 2GB body) but BEFORE the route handler. Registered here so the
# decorator order — outermost first, innermost last — places it
# between security_headers and admin_gate.
app.middleware("http")(csrf_middleware)


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
    # Hardened canonical base — read from settings.canonical_base so
    # `rel=canonical` in base.html can't be host-header-poisoned.
    request.state.canonical_base = _settings.canonical_base.rstrip("/") if _settings.canonical_base else ""
    # Skip the per-request DB round-trip for static assets and the
    # health probe — both run every few seconds and never need user
    # context.
    path = request.url.path
    if path.startswith("/static") or path == "/health":
        return await call_next(request)
    parsed = parse_session(request)
    if parsed is not None:
        uid, jti = parsed
        from sqlalchemy import func, select as _select
        from app.models import Notification, User
        async with SessionLocal() as db:
            user = await db.get(User, uid)
            # Reject the session if the user's jti has rotated (logout,
            # password change, "log out everywhere") — even though the
            # signed cookie is still cryptographically valid.
            if user is not None and user.session_jti and user.session_jti == jti:
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


# Cache the DB probe result for 2s so the per-3s Caddy probe doesn't
# hammer Postgres but UptimeRobot still gets a fresh-enough signal.
_HEALTH_CACHE: dict[str, object] = {"ok": True, "ts": 0.0}


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    """Lightweight liveness probe.

    UptimeRobot defaults to HEAD; Caddy uses GET. Both go through here.
    Includes a cached SELECT 1 against the DB so the proxy doesn't
    happily route traffic to an app instance whose database has
    silently fallen over.
    """
    import time as _time
    from sqlalchemy import text as _text
    from fastapi.responses import JSONResponse as _JSONResponse
    now = _time.time()
    if now - float(_HEALTH_CACHE["ts"]) > 2.0:
        ok = True
        try:
            async with SessionLocal() as db:
                await db.execute(_text("SELECT 1"))
        except Exception:
            ok = False
        _HEALTH_CACHE["ok"] = ok
        _HEALTH_CACHE["ts"] = now
    if _HEALTH_CACHE["ok"]:
        return {"status": "ok"}
    return _JSONResponse({"status": "db_down"}, status_code=503)
