import pytest

from app.services.workshop_scrape import fetch_comment_total, scrape_display_labels


@pytest.mark.asyncio
async def test_daycount_has_labels():
    out = await scrape_display_labels(3724689682)
    # All three numbers exist (>= 0 in case PZ ever zeroes them)
    assert out["visitors"] is not None
    assert out["subscribers"] is not None
    assert out["favorites"] is not None


@pytest.mark.asyncio
async def test_lifemilestones_has_comments():
    total = await fetch_comment_total(3721918079, "76561198279237042")
    assert total is not None
    assert total >= 4   # was 4 at brainstorm time, may grow
