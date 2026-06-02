import asyncio

import pytest

from app.services.cache import cached, invalidate


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate()
    yield
    invalidate()


@pytest.mark.asyncio
async def test_cached_invokes_producer_once():
    calls = 0
    async def producer():
        nonlocal calls
        calls += 1
        return 42

    a = await cached("k", 10, producer)
    b = await cached("k", 10, producer)
    assert a == b == 42
    assert calls == 1, "producer should only run once for repeated calls within TTL"


@pytest.mark.asyncio
async def test_cached_concurrent_callers_coalesce():
    """Two coroutines racing for the same key should share one producer call."""
    calls = 0
    started = asyncio.Event()
    proceed = asyncio.Event()

    async def slow_producer():
        nonlocal calls
        calls += 1
        started.set()
        await proceed.wait()
        return "value"

    t1 = asyncio.create_task(cached("racy", 10, slow_producer))
    await started.wait()
    t2 = asyncio.create_task(cached("racy", 10, slow_producer))
    # give t2 a chance to also block on the lock
    await asyncio.sleep(0.01)
    proceed.set()
    a, b = await asyncio.gather(t1, t2)
    assert a == b == "value"
    assert calls == 1


@pytest.mark.asyncio
async def test_invalidate_by_prefix():
    async def p(v):
        return v
    await cached("steam_app_name:108600", 10, lambda: p("PZ"))
    await cached("steam_app_name:730", 10, lambda: p("CS"))
    await cached("other:abc", 10, lambda: p("X"))

    cleared = invalidate("steam_app_name:")
    assert cleared == 2
    # other key still cached
    val = await cached("other:abc", 10, lambda: p("Y"))
    assert val == "X", "non-matching key should not be invalidated"


def test_mark_seen_dedups_within_ttl():
    from app.services.cache import mark_seen
    assert mark_seen("view:42:1.2.3.4", 1000) is True   # first time → newly seen
    assert mark_seen("view:42:1.2.3.4", 1000) is False  # within ttl → already seen
    assert mark_seen("view:99:1.2.3.4", 1000) is True   # different key → newly seen
