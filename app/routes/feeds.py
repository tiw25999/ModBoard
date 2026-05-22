"""RSS / Atom-ish feeds for power users + aggregators.

Each feed is cached for 5 minutes via the existing TTL cache so a feed
reader hitting every minute doesn't hammer the DB.
"""
from __future__ import annotations

import html as _html
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import get_db
from app.models import ForumThread, Mod, ModChangelog, ModComment, NewsPost
from app.services.cache import cached

router = APIRouter()


def _esc(s: str | None) -> str:
    return _html.escape(s or "", quote=True)


def _cdata(s: str | None) -> str:
    """Wrap a string in CDATA. Defangs any embedded `]]>` so a crafted
    comment can't close the section early and inject feed-reader markup."""
    body = (s or "").replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{body}]]>"


def _rfc822(dt: datetime | None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # RFC 822 / 2822 date format
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _rss(title: str, link: str, description: str, items: list[dict]) -> str:
    body = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        '<channel>',
        f'<title>{_esc(title)}</title>',
        f'<link>{_esc(link)}</link>',
        f'<description>{_esc(description)}</description>',
        f'<lastBuildDate>{_rfc822(datetime.now(timezone.utc))}</lastBuildDate>',
    ]
    for it in items:
        body.extend([
            '<item>',
            f'<title>{_esc(it["title"])}</title>',
            f'<link>{_esc(it["link"])}</link>',
            f'<guid isPermaLink="true">{_esc(it["link"])}</guid>',
            f'<pubDate>{_rfc822(it.get("pub_date"))}</pubDate>',
            f'<description>{_cdata(it.get("body", ""))}</description>',
            '</item>',
        ])
    body.extend(['</channel>', '</rss>'])
    return '\n'.join(body)


def _base(request: Request) -> str:
    # Prefer the configured canonical URL so an attacker can't poison
    # our cached feed XML by sending a spoofed Host header to the origin.
    if settings.canonical_base:
        return settings.canonical_base.rstrip("/")
    return f"{request.url.scheme}://{request.url.netloc}"


@router.get("/forum.rss", response_class=Response)
async def forum_rss(request: Request, session: AsyncSession = Depends(get_db)):
    base = _base(request)
    async def _build():
        rows = (await session.execute(
            select(ForumThread).order_by(ForumThread.created_at.desc()).limit(50)
        )).scalars().all()
        items = [{
            "title": t.title,
            "link": f"{base}/forum/{t.id}/{t.slug}",
            "body": t.body_raw,
            "pub_date": t.created_at,
        } for t in rows]
        return _rss(
            "ModBoard Forum — newest threads",
            f"{base}/forum",
            "Community discussion threads from ModBoard.",
            items,
        )
    xml = await cached(f"rss:forum:{base}", 300, _build)
    return Response(xml, media_type="application/rss+xml")


@router.get("/news.rss", response_class=Response)
async def news_rss(request: Request, session: AsyncSession = Depends(get_db)):
    base = _base(request)
    async def _build():
        rows = (await session.execute(
            select(NewsPost).where(NewsPost.active.is_(True))
            .order_by(NewsPost.created_at.desc()).limit(50)
        )).scalars().all()
        items = [{
            "title": p.title,
            "link": f"{base}/news#post-{p.id}",
            "body": p.body_html,
            "pub_date": p.created_at,
        } for p in rows]
        return _rss(
            "ModBoard News",
            f"{base}/news",
            "Announcements and dev log from ModBoard.",
            items,
        )
    xml = await cached(f"rss:news:{base}", 300, _build)
    return Response(xml, media_type="application/rss+xml")


@router.get("/mod/{mod_id}/comments.rss", response_class=Response)
async def mod_comments_rss(
    request: Request, mod_id: int, session: AsyncSession = Depends(get_db)
):
    mod = await session.get(Mod, mod_id)
    if mod is None or not mod.public:
        raise HTTPException(404)
    base = _base(request)
    async def _build():
        rows = (await session.execute(
            select(ModComment)
            .where(ModComment.mod_id == mod_id)
            .order_by(ModComment.posted_at.desc().nulls_last())
            .limit(50)
        )).scalars().all()
        items = [{
            "title": f"{c.author_name or 'anon'} — {(c.body_html or '')[:80]}",
            "link": f"{base}/mod/{mod_id}/comments",
            "body": c.body_html,
            "pub_date": c.posted_at,
        } for c in rows]
        return _rss(
            f"{mod.title or mod.name} — Workshop comments",
            f"{base}/mod/{mod_id}/comments",
            "Latest Steam Workshop comments scraped by ModBoard.",
            items,
        )
    xml = await cached(f"rss:comments:{mod_id}:{base}", 300, _build)
    return Response(xml, media_type="application/rss+xml")


@router.get("/mod/{mod_id}/changelog.rss", response_class=Response)
async def mod_changelog_rss(
    request: Request, mod_id: int, session: AsyncSession = Depends(get_db)
):
    mod = await session.get(Mod, mod_id)
    if mod is None or not mod.public:
        raise HTTPException(404)
    base = _base(request)
    async def _build():
        rows = (await session.execute(
            select(ModChangelog)
            .where(ModChangelog.mod_id == mod_id)
            .order_by(ModChangelog.posted_at.desc().nulls_last())
            .limit(30)
        )).scalars().all()
        items = [{
            "title": ch.headline or "Update",
            "link": f"{base}/mod/{mod_id}/changelog",
            "body": ch.body_html,
            "pub_date": ch.posted_at,
        } for ch in rows]
        return _rss(
            f"{mod.title or mod.name} — Release notes",
            f"{base}/mod/{mod_id}/changelog",
            "Steam Workshop release notes scraped by ModBoard.",
            items,
        )
    xml = await cached(f"rss:changelog:{mod_id}:{base}", 300, _build)
    return Response(xml, media_type="application/rss+xml")
