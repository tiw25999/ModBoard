"""Pure-function unit tests for the Steam BBCode renderer.
No external services, no DB."""
from app.services.bbcode import steam_bbcode_to_html


def test_empty_returns_empty():
    assert steam_bbcode_to_html("") == ""
    assert steam_bbcode_to_html(None) == ""


def test_plain_text_wraps_in_paragraph():
    out = steam_bbcode_to_html("Hello world")
    assert out == "<p>Hello world</p>"


def test_double_newline_makes_two_paragraphs():
    out = steam_bbcode_to_html("First.\n\nSecond.")
    assert "<p>First.</p>" in out
    assert "<p>Second.</p>" in out


def test_single_newline_becomes_br():
    out = steam_bbcode_to_html("Line 1\nLine 2")
    assert "Line 1<br>Line 2" in out


def test_bold_italic_inline():
    out = steam_bbcode_to_html("[b]bold[/b] and [i]italic[/i]")
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out


def test_headers_downgrade():
    out = steam_bbcode_to_html("[h1]Big[/h1]\n\n[h2]Sub[/h2]\n\n[h3]Tiny[/h3]")
    # h1→h3, h2→h4, h3→h5 to avoid clashing with page h1
    assert "<h3>Big</h3>" in out
    assert "<h4>Sub</h4>" in out
    assert "<h5>Tiny</h5>" in out


def test_list_items():
    out = steam_bbcode_to_html("[list][*]one[*]two[*]three[/list]")
    assert "<ul>" in out and "</ul>" in out
    assert out.count("<li>") == 3
    assert "<li>one</li>" in out


def test_url_with_label():
    out = steam_bbcode_to_html("see [url=https://example.com]docs[/url]")
    assert '<a href="https://example.com" target="_blank" rel="noopener">docs</a>' in out


def test_hr_renders():
    out = steam_bbcode_to_html("above[hr]below")
    assert "<hr>" in out


def test_escapes_raw_html():
    """User-supplied text must not inject tags."""
    out = steam_bbcode_to_html("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_quote_block():
    out = steam_bbcode_to_html("[quote]hi[/quote]")
    assert "<blockquote>hi</blockquote>" in out


def test_real_steam_description_doesnt_crash():
    """Smoke test against an excerpt of a real Workshop description."""
    sample = (
        "Track your survivor's journey across 17 activity categories.\n\n"
        "[hr][h1]MILESTONE CATEGORIES[/h1][hr]\n\n"
        "[h2]Combat & Survival[/h2]\n"
        "[list]\n"
        "[*][b]Combat[/b] - Zombies killed. Tiers: 50 / 200 / 500 / 2,000\n"
        "[*][b]Survival[/b] - Days survived.\n"
        "[/list]"
    )
    out = steam_bbcode_to_html(sample)
    assert "<h3>MILESTONE CATEGORIES</h3>" in out
    assert "<h4>Combat &amp; Survival</h4>" in out
    assert "<strong>Combat</strong>" in out
