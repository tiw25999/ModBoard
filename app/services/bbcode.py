"""Minimal Steam-flavored BBCode → HTML renderer.

Covers the tags the Steam Workshop description / changelog actually emits:
  [h1] [h2] [h3] [b] [i] [u] [strike] [hr] [url=...] [img] [quote]
  [code] [list] / [*] / [/list] (and [olist])

Plain newlines become <br>; lines inside <pre>/<code> keep their whitespace.
Unknown tags are left as literal text (escaped) so nothing breaks visibly.
"""
from __future__ import annotations

import html as _html
import re

_HEADER_MAP = {"h1": "h3", "h2": "h4", "h3": "h5"}

# Scheme allowlist for [url=...] and [img] BBCode tags. Anything else
# (notably `javascript:`, `data:`, `vbscript:`) gets neutered to "#"
# to prevent stored XSS when a malicious Steam Workshop author drops
# `[url=javascript:alert(document.cookie)]click[/url]` into their
# mod description.
_SAFE_URL_RE = re.compile(r"^\s*(https?://|mailto:|/|#)", re.IGNORECASE)


def _safe_url(raw: str) -> str:
    """Return an escaped URL if its scheme is safe, else `#`."""
    if _SAFE_URL_RE.match(raw):
        return _esc(raw.strip())
    return "#"


def _esc(s: str) -> str:
    return _html.escape(s, quote=True)


def _render_inline(text: str) -> str:
    """Apply inline tags. Run after block-level transforms."""
    # url with explicit target
    text = re.sub(
        r"\[url=([^\]]+)\](.*?)\[/url\]",
        lambda m: f'<a href="{_safe_url(m.group(1))}" target="_blank" rel="noopener">{m.group(2)}</a>',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # bare url
    text = re.sub(
        r"\[url\](.*?)\[/url\]",
        lambda m: f'<a href="{_safe_url(m.group(1))}" target="_blank" rel="noopener">{_esc(m.group(1))}</a>',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"\[b\](.*?)\[/b\]", r"<strong>\1</strong>", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\[i\](.*?)\[/i\]", r"<em>\1</em>", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\[u\](.*?)\[/u\]", r"<u>\1</u>", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\[strike\](.*?)\[/strike\]", r"<s>\1</s>", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(
        r"\[img\](.*?)\[/img\]",
        lambda m: f'<img src="{_safe_url(m.group(1))}" alt="" loading="lazy">',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return text


def _render_lists(text: str) -> str:
    def _replace(match: re.Match, tag: str) -> str:
        body = match.group(1)
        # Split on [*]; first chunk is preamble (usually empty/whitespace)
        parts = re.split(r"\[\*\]", body)
        items = [p.strip() for p in parts[1:] if p.strip()]
        if not items:
            return ""
        rendered = "".join(f"<li>{item}</li>" for item in items)
        return f"<{tag}>{rendered}</{tag}>"

    text = re.sub(
        r"\[list\](.*?)\[/list\]",
        lambda m: _replace(m, "ul"),
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"\[olist\](.*?)\[/olist\]",
        lambda m: _replace(m, "ol"),
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return text


def steam_bbcode_to_html(text: str | None) -> str:
    if not text:
        return ""
    # Escape literal HTML first so user content can't inject tags
    s = _esc(text)
    # Code blocks: pull out, render last so we don't mangle their content
    code_blocks: list[str] = []

    def _stash_code(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CODE{len(code_blocks) - 1}\x00"

    s = re.sub(r"\[code\](.*?)\[/code\]", _stash_code, s, flags=re.IGNORECASE | re.DOTALL)

    # Headers — downgrade since the page already has h1/h2
    for src, dst in _HEADER_MAP.items():
        s = re.sub(
            rf"\[{src}\](.*?)\[/{src}\]",
            rf"<{dst}>\1</{dst}>",
            s,
            flags=re.IGNORECASE | re.DOTALL,
        )

    s = re.sub(r"\[hr\](\[/hr\])?", "<hr>", s, flags=re.IGNORECASE)
    s = re.sub(
        r"\[quote(?:=[^\]]+)?\](.*?)\[/quote\]",
        r"<blockquote>\1</blockquote>",
        s,
        flags=re.IGNORECASE | re.DOTALL,
    )

    s = _render_lists(s)
    s = _render_inline(s)

    # Wrap remaining plain text in paragraphs at double newlines
    paragraphs = re.split(r"\n{2,}", s)
    out: list[str] = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        # If the paragraph is already a single block element, don't wrap
        if re.match(r"^\s*<(h[1-6]|ul|ol|blockquote|hr|pre)", p, re.IGNORECASE):
            out.append(p)
        else:
            out.append("<p>" + p.replace("\n", "<br>") + "</p>")
    rendered = "\n".join(out)

    # Restore code blocks
    def _unstash(m: re.Match) -> str:
        idx = int(m.group(1))
        body = code_blocks[idx]
        return f"<pre><code>{body}</code></pre>"

    rendered = re.sub(r"\x00CODE(\d+)\x00", _unstash, rendered)
    return rendered
