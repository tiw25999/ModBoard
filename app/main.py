"""ModBoard FastAPI app."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.db import SessionLocal
from app.routes import admin as admin_routes
from app.routes import auth as auth_routes
from app.routes import forum as forum_routes
from app.routes import public as public_routes
from app.services.auth import ADMIN_COOKIE, ADMIN_COOKIE_MAX_AGE, check_basic_admin_header
from app.services.poller import poller_task
from app.services.session import session_user_id

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poller_task())
    yield
    task.cancel()


app = FastAPI(title="ModBoard", lifespan=lifespan)


@app.middleware("http")
async def admin_cookie_middleware(request: Request, call_next):
    """After any successful /admin/* request that carried valid Basic auth,
    stamp the marker cookie so the rest of the site can render the Admin
    badge + Logout link even on pages the browser won't auto-send the
    Basic header to."""
    response = await call_next(request)
    if (
        request.url.path.startswith("/admin/")
        and 200 <= response.status_code < 400
        and check_basic_admin_header(request.headers.get("authorization"))
    ):
        response.set_cookie(
            ADMIN_COOKIE,
            "1",
            max_age=ADMIN_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=False,
        )
    return response

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(public_routes.router)
app.include_router(forum_routes.router)
app.include_router(auth_routes.router)
app.include_router(admin_routes.public_admin_router)  # /admin/logout — no auth
app.include_router(admin_routes.router)


@app.middleware("http")
async def attach_current_user(request: Request, call_next):
    """Look up the logged-in user once per request so templates can read
    `request.state.user` without a per-template DB round-trip."""
    request.state.user = None
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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
