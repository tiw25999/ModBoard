import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.db import SessionLocal
from app.models import Mod, ModChangelog, ModComment, ModDiscussion, ModSnapshot
from app.services.steam_api import get_app_name, get_published_file_details
from app.services.workshop_scrape import (
    fetch_changelog,
    fetch_comments,
    fetch_discussions,
    scrape_display_labels,
)

log = logging.getLogger(__name__)


async def poll_once() -> None:
    async with SessionLocal() as session:
        mods = (await session.execute(select(Mod))).scalars().all()
        if not mods:
            log.info("no mods to poll")
            return
        details = await get_published_file_details([m.id for m in mods])
        by_id = {int(d["publishedfileid"]): d for d in details}
        for i, mod in enumerate(mods):
            # Stagger to dodge Steam community 429s — each mod fans out to 4
            # scrape requests; 2s between mods has been enough in practice.
            if i > 0:
                await asyncio.sleep(2.0)
            api = by_id.get(mod.id) or {}
            try:
                labels = await scrape_display_labels(mod.id)
            except Exception as e:
                log.warning("scrape failed for %s: %s", mod.id, e)
                labels = {"visitors": None, "subscribers": None, "favorites": None}
            try:
                comments_total, scraped_comments = await fetch_comments(
                    mod.id, settings.steam_creator_id, count=50
                )
            except Exception as e:
                log.warning("comment fetch failed for %s: %s", mod.id, e)
                comments_total, scraped_comments = None, []
            if scraped_comments:
                now = datetime.now(timezone.utc)
                stmt = pg_insert(ModComment).values([
                    {
                        "mod_id": mod.id,
                        "comment_id": c["comment_id"],
                        "author_name": c["author_name"],
                        "author_profile_url": c["author_profile_url"],
                        "author_avatar_url": c["author_avatar_url"],
                        "body_html": c["body_html"],
                        "posted_at": c["posted_at"],
                        "scraped_at": now,
                    }
                    for c in scraped_comments
                ])
                stmt = stmt.on_conflict_do_update(
                    index_elements=["comment_id"],
                    set_={
                        "body_html": stmt.excluded.body_html,
                        "author_name": stmt.excluded.author_name,
                        "author_avatar_url": stmt.excluded.author_avatar_url,
                        "scraped_at": stmt.excluded.scraped_at,
                    },
                )
                await session.execute(stmt)
            snap = ModSnapshot(
                mod_id=mod.id,
                captured_at=datetime.now(timezone.utc),
                subscriptions=api.get("subscriptions"),
                lifetime_subs=api.get("lifetime_subscriptions"),
                favorited=api.get("favorited"),
                views=api.get("views"),
                comments_count=comments_total,
                last_updated=(
                    datetime.fromtimestamp(api["time_updated"], tz=timezone.utc)
                    if api.get("time_updated") else None
                ),
                visitors_display=labels["visitors"],
                subscribers_display=labels["subscribers"],
                favorites_display=labels["favorites"],
            )
            session.add(snap)
            # sync title / thumbnail / description from API (description always
            # refreshed in case the author edits the Workshop page)
            if api.get("title") and not mod.title:
                mod.title = api["title"]
            if api.get("preview_url") and not mod.thumbnail_url:
                mod.thumbnail_url = api["preview_url"]
            if api.get("description") is not None:
                mod.description = api["description"]

            # parent app — only resolve name when we see a new app_id we don't
            # know yet, so we don't hammer the Steam Store API every poll.
            api_app_id = api.get("consumer_app_id")
            if api_app_id and mod.app_id != api_app_id:
                mod.app_id = api_app_id
                mod.app_name = None  # invalidate cached name on app change
            if mod.app_id and not mod.app_name:
                mod.app_name = await get_app_name(mod.app_id)

            try:
                discussion_entries = await fetch_discussions(mod.id)
            except Exception as e:
                log.warning("discussions fetch failed for %s: %s", mod.id, e)
                discussion_entries = []
            if discussion_entries:
                now = datetime.now(timezone.utc)
                stmt = pg_insert(ModDiscussion).values([
                    {
                        "mod_id": mod.id,
                        "thread_id": d["thread_id"],
                        "title": d["title"],
                        "body_preview": d["body_preview"],
                        "author_name": d["author_name"],
                        "url": d["url"],
                        "reply_count": d["reply_count"],
                        "last_post_at": d["last_post_at"],
                        "last_post_author": d["last_post_author"],
                        "first_seen_at": now,
                        "scraped_at": now,
                    }
                    for d in discussion_entries
                ])
                stmt = stmt.on_conflict_do_update(
                    index_elements=["thread_id"],
                    set_={
                        "title": stmt.excluded.title,
                        "body_preview": stmt.excluded.body_preview,
                        "reply_count": stmt.excluded.reply_count,
                        "last_post_at": stmt.excluded.last_post_at,
                        "last_post_author": stmt.excluded.last_post_author,
                        "scraped_at": stmt.excluded.scraped_at,
                    },
                )
                await session.execute(stmt)

            try:
                changelog_entries = await fetch_changelog(mod.id)
            except Exception as e:
                log.warning("changelog fetch failed for %s: %s", mod.id, e)
                changelog_entries = []
            if changelog_entries:
                now = datetime.now(timezone.utc)
                stmt = pg_insert(ModChangelog).values([
                    {
                        "mod_id": mod.id,
                        "post_id": ch["post_id"],
                        "headline": ch["headline"],
                        "author_name": ch["author_name"],
                        "author_profile_url": ch["author_profile_url"],
                        "body_html": ch["body_html"],
                        "posted_at": ch["posted_at"],
                        "scraped_at": now,
                    }
                    for ch in changelog_entries
                ])
                stmt = stmt.on_conflict_do_update(
                    index_elements=["post_id"],
                    set_={
                        "body_html": stmt.excluded.body_html,
                        "headline": stmt.excluded.headline,
                        "posted_at": stmt.excluded.posted_at,
                        "scraped_at": stmt.excluded.scraped_at,
                    },
                )
                await session.execute(stmt)
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
