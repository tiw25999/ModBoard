import re

import httpx
from bs4 import BeautifulSoup


PAGE_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}"
COMMENT_URL = (
    "https://steamcommunity.com/comment/PublishedFile_Public/render/"
    "{creator_id}/{mod_id}/"
)
UA = "Mozilla/5.0 (compatible; ModBoard/0.1)"


def _num(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


async def scrape_display_labels(mod_id: int) -> dict[str, int | None]:
    """Return Unique Visitors / Current Subscribers / Current Favorites
    as the user sees them on the Workshop page."""
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": UA}) as client:
        r = await client.get(PAGE_URL.format(mod_id=mod_id))
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    out: dict[str, int | None] = {"visitors": None, "subscribers": None, "favorites": None}
    for stat in soup.select(".stats_table tr"):
        cells = stat.find_all("td")
        if len(cells) != 2:
            continue
        value, label = cells[0].get_text(strip=True), cells[1].get_text(strip=True)
        n = _num(value)
        lab = label.lower()
        if "visitor" in lab:
            out["visitors"] = n
        elif "subscriber" in lab:
            out["subscribers"] = n
        elif "favorite" in lab:
            out["favorites"] = n
    return out


async def fetch_comment_total(mod_id: int, creator_id: str) -> int | None:
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": UA}) as client:
        r = await client.post(
            COMMENT_URL.format(creator_id=creator_id, mod_id=mod_id),
            data={"count": 1, "start": 0, "oldestfirst": 1},
        )
        r.raise_for_status()
        data = r.json()
    return int(data.get("total_count")) if data.get("success") else None
