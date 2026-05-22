"""HTML sanitization for content we did NOT author.

Used for Steam Workshop comments, changelog bodies, and discussion
previews — Steam normally sanitizes its own output, but we run bleach
as defense in depth so a future Steam regression (or a missed BBCode
edge case) can't turn into stored XSS on our pages.
"""
from __future__ import annotations

import bleach

# Steam comment / changelog markup we want to keep.
_ALLOWED_TAGS = {
    "a", "b", "strong", "i", "em", "u", "s", "strike",
    "br", "p", "div", "span",
    "ul", "ol", "li",
    "blockquote", "pre", "code",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "img", "hr",
}

_ALLOWED_ATTRS = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height", "loading"],
    "span": ["class"],
    "div": ["class"],
    "*": [],
}

# Only http/https URLs allowed for href + src — blocks javascript:,
# data:, vbscript:, file: etc.
_ALLOWED_PROTOCOLS = {"http", "https", "mailto"}


def clean_steam_html(html: str | None) -> str:
    """Sanitize HTML scraped from Steam pages. Returns empty string on
    None/empty input. Safe to render with `|safe` afterwards."""
    if not html:
        return ""
    cleaned = bleach.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,            # drop forbidden tags entirely, don't escape them
        strip_comments=True,
    )
    return cleaned
