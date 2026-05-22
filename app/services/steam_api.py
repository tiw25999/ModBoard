"""Steam Web API wrappers.

We use TWO published-file endpoints:

  1. IPublishedFileService/GetDetails/v1 (GET, NEW — preferred)
     Returns the full PublishedFileDetails proto: subscriptions,
     lifetime_subscriptions, favorited, lifetime_favorited, followers,
     lifetime_followers, views, vote_data {votes_up, votes_down, score},
     num_comments_public, tags, children, kvtags, banned/ban_reason.

  2. ISteamRemoteStorage/GetPublishedFileDetails/v1 (POST, LEGACY — kept
     as fallback when STEAM_WEB_API_KEY isn't set, since GetDetails
     without a key gets a tiny shared bucket).

The legacy endpoint's `subscriptions` / `favorited` fields are stale-
cache-prone and frequently come back null for less-popular items —
that's the root of the "—" we kept seeing on the mod list.
"""
from typing import TypedDict

import httpx

from app.config import settings


class VoteData(TypedDict, total=False):
    score: float
    votes_up: int
    votes_down: int


class FileDetails(TypedDict, total=False):
    publishedfileid: str
    result: int
    title: str
    description: str
    short_description: str
    creator: str
    creator_appid: int
    consumer_app_id: int  # legacy field name
    consumer_appid: int   # new field name
    file_size: int
    preview_url: str
    time_created: int
    time_updated: int
    subscriptions: int
    lifetime_subscriptions: int
    favorited: int
    lifetime_favorited: int
    followers: int
    lifetime_followers: int
    views: int
    visibility: int
    banned: int
    ban_reason: str
    num_comments_public: int
    num_children: int
    tags: list[dict]
    children: list[dict]
    kvtags: list[dict]
    vote_data: VoteData


LEGACY_URL = (
    "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
)
MODERN_URL = (
    "https://api.steampowered.com/IPublishedFileService/GetDetails/v1/"
)
APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"


async def _get_details_modern(file_ids: list[int]) -> list[FileDetails]:
    """IPublishedFileService/GetDetails — needs an API key for the full
    rate budget. Returns much richer data than the legacy endpoint."""
    params: list[tuple[str, str | int]] = [
        ("key", settings.steam_web_api_key),
        ("includetags", 1),
        ("includekvtags", 1),
        ("includechildren", 1),
        ("includeadditionalpreviews", 0),
        ("includevotes", 1),
        ("includereactions", 0),
        ("includemetadata", 0),
        ("strip_description_bbcode", 0),
    ]
    for i, fid in enumerate(file_ids):
        params.append((f"publishedfileids[{i}]", fid))
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(MODERN_URL, params=params)
        r.raise_for_status()
        payload = r.json()
    rows = payload.get("response", {}).get("publishedfiledetails", [])
    # Normalise field name drift between the two endpoints so callers
    # don't have to care which path produced the row.
    for row in rows:
        if "consumer_appid" in row and "consumer_app_id" not in row:
            row["consumer_app_id"] = row["consumer_appid"]
    return rows


async def _get_details_legacy(file_ids: list[int]) -> list[FileDetails]:
    """ISteamRemoteStorage/GetPublishedFileDetails — no key required.
    Used as a fallback when STEAM_WEB_API_KEY is empty."""
    body: dict[str, str | int] = {"itemcount": len(file_ids)}
    for i, fid in enumerate(file_ids):
        body[f"publishedfileids[{i}]"] = fid
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(LEGACY_URL, data=body)
        r.raise_for_status()
        payload = r.json()
    return payload["response"]["publishedfiledetails"]


async def get_published_file_details(file_ids: list[int]) -> list[FileDetails]:
    """Fetch published-file metadata for a batch of workshop IDs.

    Routes to the modern key-authenticated endpoint when STEAM_WEB_API_KEY
    is set, falls back to the legacy unauthenticated one otherwise.
    """
    if not file_ids:
        return []
    if settings.steam_web_api_key:
        try:
            return await _get_details_modern(file_ids)
        except (httpx.HTTPError, KeyError):
            # On any failure of the modern endpoint, fall back to legacy
            # rather than skipping the whole poll cycle.
            pass
    return await _get_details_legacy(file_ids)


async def _fetch_app_name_uncached(app_id: int) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                APP_DETAILS_URL,
                params={"appids": app_id, "filters": "basic", "l": "english"},
            )
            r.raise_for_status()
            payload = r.json()
        entry = payload.get(str(app_id)) or {}
        if not entry.get("success"):
            return None
        return entry.get("data", {}).get("name")
    except Exception:
        return None


async def get_app_name(app_id: int) -> str | None:
    """Look up a Steam app's display name from the public Store API.
    Cached for a day — app names virtually never change, and the Store
    API is rate-limited (~200 req / 5min per IP)."""
    from app.services.cache import cached
    return await cached(
        f"steam_app_name:{app_id}",
        ttl_seconds=24 * 60 * 60,
        producer=lambda: _fetch_app_name_uncached(app_id),
    )
