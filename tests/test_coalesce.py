"""Unit test — coalesce decorator collapses a concurrent herd into one call.

Context: scanner fans out per-strategy; 50+ strategies fetching the same event/
ticks on one candle must collapse to a single network request (single-flight),
else 50x redundant GETs hammer Gamma/CLOB on every busy candle.
Source: polybot.runtime.coalesce.coalesce.
Expected: N concurrent identical calls → underlying fn runs once; distinct args →
distinct runs; ttl expiry → re-run; exceptions propagate and are NOT cached.
"""
import asyncio
import pytest
from polybot.runtime.coalesce import coalesce


def test_concurrent_herd_collapses_to_one():
    calls = {"n": 0}

    @coalesce(ttl_s=10.0)
    async def fetch(x):
        calls["n"] += 1
        await asyncio.sleep(0.02)          # hold in-flight so the herd piles on
        return x * 2

    res = asyncio.run(_gather([lambda: fetch(7) for _ in range(50)]))
    assert res == [14] * 50                # every caller gets the right result...
    assert calls["n"] == 1                 # ...from ONE underlying fetch


def test_distinct_args_dont_collapse():
    calls = {"n": 0}

    @coalesce(ttl_s=10.0)
    async def fetch(x):
        calls["n"] += 1
        await asyncio.sleep(0.01)
        return x

    res = asyncio.run(_gather([lambda: fetch(1), lambda: fetch(2), lambda: fetch(1)]))
    assert sorted(res) == [1, 1, 2]
    assert calls["n"] == 2                 # key=1 collapsed (2 callers), key=2 separate


def test_ttl_expiry_refetches():
    calls = {"n": 0}

    @coalesce(ttl_s=0.05)
    async def fetch():
        calls["n"] += 1
        return calls["n"]

    async def run():
        a = await fetch()                  # miss → fetch #1
        b = await fetch()                  # within ttl → cached
        await asyncio.sleep(0.08)          # ttl expires
        c = await fetch()                  # miss → fetch #2
        return a, b, c

    a, b, c = asyncio.run(run())
    assert (a, b, c) == (1, 1, 2) and calls["n"] == 2


def test_exception_not_cached():
    calls = {"n": 0}

    @coalesce(ttl_s=10.0)
    async def fetch():
        calls["n"] += 1
        raise ValueError("boom")

    async def run():
        for _ in range(2):
            with pytest.raises(ValueError):
                await fetch()
        return calls["n"]

    assert asyncio.run(run()) == 2         # each call re-runs (failures never cached)


async def _gather(factories):
    return await asyncio.gather(*[f() for f in factories])
