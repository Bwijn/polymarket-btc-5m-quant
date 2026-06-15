"""Single-flight + short-TTL coalescing for async read fetches (collapse a herd).

Context: scanner fans out per-strategy; on a busy candle 50+ strategies wake at
the SAME cs+entry_offset and each independently GET the same event / ticks /
klines (50x redundant network). This decorator makes concurrent identical calls
share ONE in-flight awaitable (single-flight), then lingers the result ttl_s to
absorb ms-jittered stragglers. Keep ttl_s well under the settle poll (30s) so
the mutable event fields (closed / up_won) never stale-out settlement.
Source: wraps pm_api.fetch_event, bn_api.fetch_klines.
Expected: N concurrent identical calls → 1 underlying call; distinct args →
distinct calls; ttl expiry → re-fetch; exceptions propagate, never cached.
"""
from __future__ import annotations
import asyncio
import time
from functools import wraps
from typing import Awaitable, Callable


def coalesce(ttl_s: float) -> Callable:
    """Dedup concurrent identical async calls + cache the result for ttl_s."""
    inflight: dict = {}
    cached: dict = {}                       # key -> (expiry_monotonic, result)

    def deco(fn: Callable[..., Awaitable]):
        @wraps(fn)
        async def wrap(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            hit = cached.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]
            fut = inflight.get(key)
            if fut is not None:
                return await fut            # ride the in-flight request (the herd)
            fut = asyncio.ensure_future(fn(*args, **kwargs))
            inflight[key] = fut             # register BEFORE first await yields
            try:
                res = await fut
            finally:
                inflight.pop(key, None)     # clear even on error (don't cache failures)
            cached[key] = (now + ttl_s, res)
            if len(cached) > 64:            # opportunistic prune — bounds long-run memory
                for k, (exp, _) in list(cached.items()):
                    if exp <= now:
                        del cached[k]
            return res
        return wrap
    return deco
