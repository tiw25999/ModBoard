from typing import TypedDict

import httpx


class FileDetails(TypedDict, total=False):
    publishedfileid: str
    title: str
    description: str
    creator: str
    consumer_app_id: int
    file_size: int
    preview_url: str
    time_created: int
    time_updated: int
    subscriptions: int
    lifetime_subscriptions: int
    favorited: int
    views: int
    visibility: int
    banned: int


GET_DETAILS_URL = (
    "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
)
APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"


async def get_published_file_details(file_ids: list[int]) -> list[FileDetails]:
    body: dict[str, str | int] = {"itemcount": len(file_ids)}
    for i, fid in enumerate(file_ids):
        body[f"publishedfileids[{i}]"] = fid
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(GET_DETAILS_URL, data=body)
        r.raise_for_status()
        payload = r.json()
    return payload["response"]["publishedfiledetails"]


async def get_app_name(app_id: int) -> str | None:
    """Look up a Steam app's display name from the public Store API.
    Returns None on any failure — caller should treat as "unknown" and retry next poll."""
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
