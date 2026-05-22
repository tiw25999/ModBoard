from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Mod, ModComment, ModSnapshot

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


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, session: AsyncSession = Depends(get_db)):
    mods = (
        await session.execute(select(Mod).where(Mod.public.is_(True)).order_by(Mod.name))
    ).scalars().all()
    rows = [{"mod": m, "snap": await _latest_snapshot(session, m.id)} for m in mods]
    return templates.TemplateResponse(request, "mod_list.html", {"mods": rows})


@router.get("/mod/{mod_id}", response_class=HTMLResponse)
async def mod_detail(
    request: Request, mod_id: int, session: AsyncSession = Depends(get_db)
):
    mod = await session.get(Mod, mod_id)
    if mod is None or not mod.public:
        raise HTTPException(404)
    snap = await _latest_snapshot(session, mod_id)
    comments = (
        await session.execute(
            select(ModComment)
            .where(ModComment.mod_id == mod_id)
            .order_by(ModComment.posted_at.desc().nulls_last())
            .limit(50)
        )
    ).scalars().all()
    return templates.TemplateResponse(
        request, "mod_detail.html",
        {"mod": mod, "snap": snap, "comments": comments},
    )


@router.get("/mod/{mod_id}/stats", response_class=HTMLResponse)
async def mod_stats(
    request: Request, mod_id: int, session: AsyncSession = Depends(get_db)
):
    mod = await session.get(Mod, mod_id)
    if mod is None or not mod.public:
        raise HTTPException(404)
    q = (
        select(ModSnapshot)
        .where(ModSnapshot.mod_id == mod_id)
        .order_by(ModSnapshot.captured_at.asc())
    )
    snaps = (await session.execute(q)).scalars().all()
    labels = [s.captured_at.strftime("%Y-%m-%d %H:%M") for s in snaps]
    subs   = [s.subscribers_display for s in snaps]
    return templates.TemplateResponse(
        request, "mod_stats.html",
        {"mod": mod, "labels": labels, "subs": subs}
    )
