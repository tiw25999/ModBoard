from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import Response

from app.db import get_db
from app.models import Mod
from app.services.auth import ADMIN_COOKIE, require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
# Routes that should NOT trigger an auth prompt (logout, etc.) live on a
# separate router so they bypass the require_admin dependency.
public_admin_router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")


@public_admin_router.get("/logout")
async def logout():
    """Best-effort admin logout. Clears the UI marker cookie and returns a
    page that explains the Basic-auth limitation (browser must drop creds
    on its own — usually by closing the tab)."""
    response = RedirectResponse("/?logged_out=1", status_code=303)
    response.delete_cookie(ADMIN_COOKIE, samesite="lax")
    return response


@router.get("/mods", response_class=HTMLResponse)
async def list_mods(request: Request, session: AsyncSession = Depends(get_db)):
    mods = (await session.execute(select(Mod).order_by(Mod.name))).scalars().all()
    return templates.TemplateResponse(request, "admin_mods.html", {"mods": mods})


@router.post("/mods")
async def add_mod(
    workshop_id: int = Form(...),
    name: str = Form(...),
    session: AsyncSession = Depends(get_db),
):
    mod = Mod(
        id=workshop_id,
        name=name,
        workshop_url=f"https://steamcommunity.com/sharedfiles/filedetails/?id={workshop_id}",
        created_at=datetime.now(timezone.utc),
    )
    session.add(mod)
    await session.commit()
    return RedirectResponse("/admin/mods", status_code=303)


@router.post("/mods/{mod_id}/delete")
async def delete_mod(mod_id: int, session: AsyncSession = Depends(get_db)):
    mod = await session.get(Mod, mod_id)
    if mod is not None:
        await session.delete(mod)
        await session.commit()
    return RedirectResponse("/admin/mods", status_code=303)
