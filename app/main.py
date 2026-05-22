"""ModBoard FastAPI app."""
import asyncio
import logging
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
from app.routes import seo as seo_routes
from app.services.auth import is_admin as _is_admin_cookie
from app.services.poller import poller_task
from app.services.session import session_user_id

_error_templates = Jinja2Templates(directory="app/templates")

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poller_task())
    yield
    task.cancel()


app = FastAPI(title="ModBoard", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(seo_routes.router)   # robots.txt + sitemap.xml at root
app.include_router(public_routes.router)
app.include_router(forum_routes.router)
app.include_router(auth_routes.router)
app.include_router(admin_routes.router)


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
    """Look up the logged-in user + admin status once per request so
    templates can read request.state.user / request.state.is_admin
    without a per-template DB round-trip."""
    request.state.user = None
    request.state.is_admin = _is_admin_cookie(request)
    uid = session_user_id(request)
    if uid is not None:
        async with SessionLocal() as db:
            from app.models import User
            user = await db.get(User, uid)
            if user is not None:
                request.state.user = {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                    "avatar_url": user.avatar_url,
                }
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
