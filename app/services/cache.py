"""Tiny in-process TTL cache. Async-safe enough for single-replica use.

Used to soften Steam community 429s by caching the slowest endpoints
(scrape_display_labels, fetch_changelog, fetch_discussions) between
poll cycles + ad-hoc page loads.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")

_store: dict[str, tuple[float, Any]] = {}
_locks: dict[str, asyncio.Lock] = {}


async def cached(
    key: str,
    ttl_seconds: int,
    producer: Callable[[], Awaitable[T]],
) -> T:
    """Return cached value if fresh, otherwise call producer and store.
    Coalesces concurrent callers for the same key so only one fetch runs."""
    now = time.monotonic()
    hit = _store.get(key)
    if hit and (now - hit[0]) < ttl_seconds:
        return hit[1]

    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        # Re-check inside the lock — another coroutine may have produced it
        hit = _store.get(key)
        if hit and (time.monotonic() - hit[0]) < ttl_seconds:
            return hit[1]
        value = await producer()
        _store[key] = (time.monotonic(), value)
        return value


def mark_seen(key: str, ttl_seconds: int) -> bool:
    """Best-effort dedup. Returns True the first time `key` is seen
    within `ttl_seconds` (and records it now), False if already seen.

    Used to dedup view/download counts per IP. Per-process state — with
    two replicas a visitor could be counted at most twice, which is an
    acceptable inflation for non-billing counters.
    """
    now = time.monotonic()
    hit = _store.get(key)
    if hit and (now - hit[0]) < ttl_seconds:
        return False
    _store[key] = (now, True)
    return True


def invalidate(prefix: str = "") -> int:
    """Drop all keys (or keys starting with `prefix`). Returns count cleared."""
    if not prefix:
        n = len(_store)
        _store.clear()
        return n
    keys = [k for k in _store if k.startswith(prefix)]
    for k in keys:
        del _store[k]
    return len(keys)
