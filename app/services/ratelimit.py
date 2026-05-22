"""In-memory token-bucket rate limit for authentication endpoints.

Lightweight on purpose — we already have Cloudflare's rate-limit rule
on /auth/admin/login (5/min), but if an attacker bypasses Cloudflare
(origin IP leak, etc.) we still want a second line. Per-process state
is fine because a) we only have two app replicas, b) the bucket size
is tiny per IP, c) admin login attempts are rare on the happy path.

Resetting the bucket on success means a legitimate admin doesn't get
locked out by their own typo.
"""
from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

# IP → list[timestamp] of recent failed attempts
_buckets: dict[str, list[float]] = defaultdict(list)
_lock = Lock()


def check_and_record(
    key: str,
    *,
    max_attempts: int = 5,
    window_seconds: int = 900,  # 15 minutes
) -> bool:
    """Return True if the request may proceed, False if rate-limited.
    Always records the attempt — call reset() on success."""
    now = time.time()
    cutoff = now - window_seconds
    with _lock:
        bucket = _buckets[key]
        bucket[:] = [t for t in bucket if t > cutoff]
        if len(bucket) >= max_attempts:
            return False
        bucket.append(now)
        return True


def reset(key: str) -> None:
    """Clear the bucket — call after a successful auth so legit users
    aren't punished for prior typos."""
    with _lock:
        _buckets.pop(key, None)


def client_ip(request) -> str:
    """Best-effort real client IP. Trusts the X-Real-IP header that
    Caddy populates from CF-Connecting-IP — that IS the real visitor.
    Falls back to request.client.host (which would be Caddy's IP
    inside docker) only if the header is missing."""
    real = request.headers.get("x-real-ip") or request.headers.get("cf-connecting-ip")
    if real:
        return real.strip().split(",", 1)[0]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"
