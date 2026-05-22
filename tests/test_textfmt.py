from app.services.textfmt import is_likely_spam, render, slugify


def test_slugify_basic():
    assert slugify("Hello World") == "hello-world"
    assert slugify("Please add Snake's Mod Pack compat!") == "please-add-snake-s-mod-pack-compat"


def test_slugify_handles_unicode_falls_back_to_default():
    assert slugify("こんにちは") == "thread"   # all non-ASCII -> stripped -> empty -> default


def test_slugify_max_len():
    assert len(slugify("x" * 200)) == 60


def test_render_escapes_html():
    out = render("<script>x</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_render_bold_italic():
    out = render("**hi** and *there*")
    assert "<strong>hi</strong>" in out
    assert "<em>there</em>" in out


def test_render_autolinks_urls():
    out = render("see https://example.com for more")
    assert '<a href="https://example.com" target="_blank" rel="noopener nofollow">' in out


def test_render_quote_block():
    out = render("> first quoted\n> still quoted")
    assert "<blockquote>" in out
    assert "first quoted" in out


def test_render_paragraph_splits_on_double_newline():
    out = render("para one\n\npara two")
    assert "<p>para one</p>" in out
    assert "<p>para two</p>" in out


def test_spam_trips_honeypot():
    assert is_likely_spam("Title here", "Body here that is long enough", "Alice", "fill-me") is not None


def test_spam_requires_minimum_lengths():
    assert is_likely_spam("hi", "body body", "A", "") is not None
    assert is_likely_spam("Real title", "body", "Alice", "") is not None


def test_clean_post_passes_spam_check():
    assert is_likely_spam("Real title here", "Real body that is long enough.", "Alice", "") is None


def test_link_flood_rejected():
    body = " ".join(f"http://e.com/{i}" for i in range(7))
    assert is_likely_spam("Real title here", body, "Alice", "") == "too many links"
