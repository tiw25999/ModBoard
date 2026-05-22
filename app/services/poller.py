import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Mod, ModSnapshot
from app.services.steam_api import get_published_file_details
from app.services.workshop_scrape import fetch_comment_total, scrape_display_labels

log = logging.getLogger(__name__)


async def poll_once() -> None:
    async with SessionLocal() as session:
        mods = (await session.execute(select(Mod))).scalars().all()
        if not mods:
            log.info("no mods to poll")
            return
        details = await get_published_file_details([m.id for m in mods])
        by_id = {int(d["publishedfileid"]): d for d in details}
        for mod in mods:
            api = by_id.get(mod.id) or {}
            try:
                labels = await scrape_display_labels(mod.id)
            except Exception as e:
                log.warning("scrape failed for %s: %s", mod.id, e)
                labels = {"visitors": None, "subscribers": None, "favorites": None}
            try:
                comments = await fetch_comment_total(mod.id, settings.steam_creator_id)
            except Exception as e:
                log.warning("comment fetch failed for %s: %s", mod.id, e)
                comments = None
            snap = ModSnapshot(
                mod_id=mod.id,
                captured_at=datetime.now(timezone.utc),
                subscriptions=api.get("subscriptions"),
                lifetime_subs=api.get("lifetime_subscriptions"),
                favorited=api.get("favorited"),
                views=api.get("views"),
                comments_count=comments,
                last_updated=(
                    datetime.fromtimestamp(api["time_updated"], tz=timezone.utc)
                    if api.get("time_updated") else None
                ),
                visitors_display=labels["visitors"],
                subscribers_display=labels["subscribers"],
                favorites_display=labels["favorites"],
            )
            session.add(snap)
            # also sync title/description on the mod row
            if api.get("title") and not mod.title:
                mod.title = api["title"]
            if api.get("preview_url") and not mod.thumbnail_url:
                mod.thumbnail_url = api["preview_url"]
        await session.commit()
        log.info("polled %d mods", len(mods))


async def poller_task() -> None:
    interval = settings.poll_interval_minutes * 60
    while True:
        try:
            await poll_once()
        except Exception:
            log.exception("poll_once crashed; will retry next interval")
        await asyncio.sleep(interval)
