"""SEO routes — robots.txt + sitemap.xml.

The sitemap is generated from the DB so it always reflects current mods
and forum threads. Cached for 1h so search-engine crawlers don't pound
the DB; manually invalidate via the cache.invalidate() helper if needed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import ForumThread, Mod
from app.services.cache import cached

router = APIRouter()


@router.get("/robots.txt", response_class=Response)
async def robots(request: Request):
    base = f"{request.url.scheme}://{request.url.netloc}"
    body = (
        "User-agent: *\n"
        "Disallow: /admin/\n"
        "Disallow: /auth/\n"
        "Allow: /\n"
        f"\nSitemap: {base}/sitemap.xml\n"
    )
    return Response(body, media_type="text/plain")


async def _build_sitemap_xml(base: str, session: AsyncSession) -> str:
    """Static URLs + every public mod + every forum thread. Mods include
    each tab (stats/comments/changelog/discussions). Capped at 5k urls
    for politeness."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls: list[tuple[str, str, str]] = []  # (loc, lastmod, changefreq)

    # Static pages
    for path, freq in [("/", "hourly"), ("/forum", "hourly"),
                        ("/dashboard", "daily"), ("/search", "monthly")]:
        urls.append((f"{base}{path}", now, freq))

    # Mods + per-mod sub-pages
    mods = (
        await session.execute(
            select(Mod).where(Mod.public.is_(True)).order_by(Mod.id)
        )
    ).scalars().all()
    for m in mods:
        for tab in ("", "/stats", "/comments", "/changelog", "/discussions"):
            urls.append((f"{base}/mod/{m.id}{tab}", now, "daily"))

    # Forum threads (newest first)
    threads = (
        await session.execute(
            select(ForumThread).order_by(ForumThread.last_post_at.desc()).limit(2000)
        )
    ).scalars().all()
    for t in threads:
        last = (t.last_post_at or t.created_at).strftime("%Y-%m-%d")
        urls.append((f"{base}/forum/{t.id}/{t.slug}", last, "weekly"))

    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod, changefreq in urls[:5000]:
        body.append(
            f"  <url><loc>{loc}</loc>"
            f"<lastmod>{lastmod}</lastmod>"
            f"<changefreq>{changefreq}</changefreq></url>"
        )
    body.append("</urlset>")
    return "\n".join(body)


@router.get("/sitemap.xml", response_class=Response)
async def sitemap(request: Request, session: AsyncSession = Depends(get_db)):
    base = f"{request.url.scheme}://{request.url.netloc}"
    xml = await cached(
        f"sitemap:{base}",
        ttl_seconds=3600,
        producer=lambda: _build_sitemap_xml(base, session),
    )
    return Response(xml, media_type="application/xml")
