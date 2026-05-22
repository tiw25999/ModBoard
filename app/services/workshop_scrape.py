import re
from datetime import datetime, timezone
from typing import TypedDict

import httpx
from bs4 import BeautifulSoup, Tag


PAGE_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}"
COMMENT_URL = (
    "https://steamcommunity.com/comment/PublishedFile_Public/render/"
    "{creator_id}/{mod_id}/"
)
CHANGELOG_URL = "https://steamcommunity.com/sharedfiles/filedetails/changelog/{mod_id}"
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


class ScrapedComment(TypedDict):
    comment_id: str
    author_name: str | None
    author_profile_url: str | None
    author_avatar_url: str | None
    body_html: str
    posted_at: datetime | None


def _parse_comments_html(html: str) -> list[ScrapedComment]:
    soup = BeautifulSoup(html, "lxml")
    out: list[ScrapedComment] = []
    for node in soup.select("div.commentthread_comment[id^='comment_']"):
        assert isinstance(node, Tag)
        cid = (node.get("id") or "").removeprefix("comment_")
        if not cid:
            continue

        author_link = node.select_one("a.commentthread_author_link")
        author_name = None
        profile_url = None
        if isinstance(author_link, Tag):
            bdi = author_link.find("bdi")
            author_name = bdi.get_text(strip=True) if bdi else author_link.get_text(strip=True)
            href = author_link.get("href")
            profile_url = href if isinstance(href, str) else None

        avatar_url = None
        avatar_img = node.select_one("div.playerAvatar img")
        if isinstance(avatar_img, Tag):
            src = avatar_img.get("src")
            if isinstance(src, str):
                avatar_url = src

        ts_node = node.select_one("div.commentthread_comment_timestamp[data-timestamp]")
        posted_at: datetime | None = None
        if isinstance(ts_node, Tag):
            ts_raw = ts_node.get("data-timestamp")
            if isinstance(ts_raw, str) and ts_raw.isdigit():
                posted_at = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)

        body_node = node.select_one("div.commentthread_comment_text")
        body_html = body_node.decode_contents().strip() if isinstance(body_node, Tag) else ""

        out.append({
            "comment_id": cid,
            "author_name": author_name,
            "author_profile_url": profile_url,
            "author_avatar_url": avatar_url,
            "body_html": body_html,
            "posted_at": posted_at,
        })
    return out


class ScrapedChangelog(TypedDict):
    post_id: str
    headline: str | None
    author_name: str | None
    author_profile_url: str | None
    body_html: str
    posted_at: datetime | None


_HEADLINE_DATE_RE = re.compile(
    r"^Update:\s*(?P<day>\d{1,2})\s+(?P<mon>[A-Za-z]+)(?:,\s*(?P<year>\d{4}))?\s*@\s*"
    r"(?P<hour>\d{1,2}):(?P<min>\d{2})\s*(?P<ampm>am|pm)",
    re.IGNORECASE,
)
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["", "January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"],
)}


def _parse_changelog_headline(text: str) -> datetime | None:
    m = _HEADLINE_DATE_RE.match(text.strip())
    if not m:
        return None
    mon_str = m.group("mon").lower()
    mon = _MONTHS.get(mon_str, 0)
    if not mon:
        # Steam sometimes abbreviates ("Sep" vs "September") — prefix match
        for name, idx in _MONTHS.items():
            if name and name.startswith(mon_str[:3]):
                mon = idx
                break
    if not mon:
        return None
    year = int(m.group("year")) if m.group("year") else datetime.now(timezone.utc).year
    hour = int(m.group("hour")) % 12
    if m.group("ampm").lower() == "pm":
        hour += 12
    try:
        return datetime(year, mon, int(m.group("day")), hour, int(m.group("min")), tzinfo=timezone.utc)
    except ValueError:
        return None


async def fetch_changelog(mod_id: int) -> list[ScrapedChangelog]:
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": UA}) as client:
        r = await client.get(CHANGELOG_URL.format(mod_id=mod_id))
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    out: list[ScrapedChangelog] = []
    for box in soup.select("div.detailBox.changeLogCtn"):
        assert isinstance(box, Tag)
        body_p = box.find("p", id=True)
        if not isinstance(body_p, Tag):
            continue
        post_id = body_p.get("id")
        if not isinstance(post_id, str) or not post_id.isdigit():
            continue
        headline_node = box.select_one(".changelog.headline")
        headline = headline_node.get_text(" ", strip=True) if isinstance(headline_node, Tag) else None
        author_node = box.select_one(".changelog.author a")
        author_name = None
        author_url = None
        if isinstance(author_node, Tag):
            author_name = author_node.get_text(strip=True)
            href = author_node.get("href")
            author_url = href if isinstance(href, str) else None
        posted_at = _parse_changelog_headline(headline or "")
        body_html = body_p.decode_contents().strip()
        out.append({
            "post_id": post_id,
            "headline": headline,
            "author_name": author_name,
            "author_profile_url": author_url,
            "body_html": body_html,
            "posted_at": posted_at,
        })
    return out


async def fetch_comments(
    mod_id: int, creator_id: str, count: int = 50
) -> tuple[int | None, list[ScrapedComment]]:
    """Return (total_count, comments) — comments are newest first per Steam default."""
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": UA}) as client:
        r = await client.post(
            COMMENT_URL.format(creator_id=creator_id, mod_id=mod_id),
            data={"count": count, "start": 0},
        )
        r.raise_for_status()
        data = r.json()
    if not data.get("success"):
        return None, []
    total = int(data["total_count"]) if data.get("total_count") is not None else None
    return total, _parse_comments_html(data.get("comments_html", ""))
