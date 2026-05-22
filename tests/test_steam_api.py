import pytest

from app.services.steam_api import get_published_file_details


@pytest.mark.asyncio
async def test_returns_three_published_pz_mods(known_mod_ids):
    details = await get_published_file_details(known_mod_ids)
    assert len(details) == 3
    titles = {d["title"] for d in details}
    assert "Weapon Enhancement" in titles
    assert "LifeMilestones" in titles
    assert "DayCount" in titles
    # PZ app id sanity
    for d in details:
        assert d["consumer_app_id"] == 108600
