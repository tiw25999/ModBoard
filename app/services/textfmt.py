"""Render user-supplied plain text → safe HTML for the forum.

No full markdown — just enough to keep posts readable without becoming
an XSS or scraping vector:

  - HTML-escape everything first
  - Auto-linkify bare URLs (http/https only)
  - **bold** and *italic* inline (simple, non-nested)
  - Line breaks: paragraph at blank line, <br> inside paragraph
  - Quote: lines starting with "> " become <blockquote>
"""
from __future__ import annotations

import html as _html
import re

_URL_RE = re.compile(r"(https?://[^\s<>\"']+)")
_MENTION_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z0-9_.-]{2,32})")


MAX_MENTIONS_PER_POST = 10


def extract_mentions(raw: str) -> list[str]:
    """Pull `@name` tokens out of raw text before rendering. Returns
    the unique lower-cased name strings (matched case-insensitively by
    caller against user.name). Capped at MAX_MENTIONS_PER_POST so a
    `@everyone`-style spam post can't fan out N×N notifications or blow
    up an IN clause."""
    if not raw:
        return []
    seen: list[str] = []
    lower_seen: set[str] = set()
    for m in _MENTION_RE.finditer(raw):
        name = m.group(1)
        key = name.lower()
        if key in lower_seen:
            continue
        lower_seen.add(key)
        seen.append(name)
        if len(seen) >= MAX_MENTIONS_PER_POST:
            break
    return seen


def slugify(text: str, max_len: int = 60) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:max_len] or "thread"


def render(text: str) -> str:
    if not text:
        return ""
    escaped = _html.escape(text.strip(), quote=True)

    # Bold / italic on the escaped text — operate on `&lt;` etc. as opaque chars
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped, flags=re.DOTALL)
    escaped = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<em>\1</em>", escaped, flags=re.DOTALL)

    # Linkify URLs
    def _link(m: re.Match) -> str:
        url = m.group(1)
        return f'<a href="{url}" target="_blank" rel="noopener nofollow">{url}</a>'
    escaped = _URL_RE.sub(_link, escaped)

    # Highlight @mentions — the route code may upgrade these to <a> tags
    # afterwards if the name resolves to a real user. Without resolution,
    # we still visually mark them.
    escaped = _MENTION_RE.sub(r'<span class="mention">@\1</span>', escaped)

    # Split into paragraphs at blank lines
    paragraphs = re.split(r"\n\s*\n", escaped)
    out: list[str] = []
    for para in paragraphs:
        para = para.strip("\n")
        if not para:
            continue
        lines = para.split("\n")
        # Block quotes: every line starts with "&gt; "
        if all(ln.startswith("&gt; ") for ln in lines):
            body = "<br>".join(ln[5:] for ln in lines)
            out.append(f"<blockquote>{body}</blockquote>")
        else:
            out.append("<p>" + "<br>".join(lines) + "</p>")
    return "\n".join(out)


def is_likely_spam(title: str, body: str, author_name: str, honeypot: str) -> str | None:
    """Return a reason string if the post looks like spam, else None.
    Cheap checks only — no external services."""
    if honeypot:  # hidden form field bots love to fill
        return "honeypot tripped"
    if not title or len(title.strip()) < 5:
        return "title too short"
    if not body or len(body.strip()) < 10:
        return "body too short"
    if not author_name or len(author_name.strip()) < 2:
        return "author name too short"
    # Crude link-flood guard
    link_count = len(_URL_RE.findall(body))
    if link_count > 5:
        return "too many links"
    return None
