"""Polymarket public read endpoints for paper-trade scanner.

Two hosts:
  Gamma  - market metadata, /events/slug/{slug}
  CLOB   - tick history, /prices-history?market={token_id}

httpx (no SDK) for read-only public endpoints — SDK (py-clob-client*) only
needed for signed write ops (place / cancel order). See CLAUDE.md.
"""
from __future__ import annotations
import json
import logging
import httpx
from typing import Optional
from polybot.runtime.config import GAMMA_API, CLOB_API, ASSET_PREFIX

log = logging.getLogger("polybot.pm_api")

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_client = httpx.AsyncClient(timeout=_TIMEOUT)


def slug_for(cs: int) -> str:
    return f"{ASSET_PREFIX}{cs}"


async def fetch_event(slug: str) -> Optional[dict]:
    """Gamma /events/slug/{slug}. Returns Event dict or None on 404.

    Caller cares about: event.markets[0].clobTokenIds (UP, DOWN tokens),
    event.markets[0].closed, event.markets[0].outcomePrices (resolves up_won).
    """
    url = f"{GAMMA_API}/events/slug/{slug}"
    try:
        r = await _client.get(url)
    except httpx.HTTPError as e:
        log.warning(f"fetch_event {slug}: network err {e.__class__.__name__}: {e}")
        return None
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        log.warning(f"fetch_event {slug}: HTTP {r.status_code}")
        return None
    return r.json()


def parse_market(event: dict) -> Optional[dict]:
    """Pull the single-market binary fields from a btc-updown-5m event.

    Returns {'market_id', 'up_token', 'down_token', 'closed', 'up_won'?}.
    up_won is set only when closed=True and outcomePrices parseable.
    """
    markets = event.get("markets") or []
    if not markets:
        return None
    m = markets[0]
    tokens = m.get("clobTokenIds")
    if isinstance(tokens, str):
        tokens = json.loads(tokens or "[]")
    if not tokens or len(tokens) < 2:
        return None
    out = {
        "market_id": m.get("conditionId", ""),
        "up_token": tokens[0],
        "down_token": tokens[1],
        "closed": bool(m.get("closed")),
        "up_won": None,
    }
    if out["closed"]:
        op = m.get("outcomePrices") or "[]"
        if isinstance(op, str):
            try:
                op = json.loads(op)
            except json.JSONDecodeError:
                op = []
        if len(op) >= 2:
            try:
                out["up_won"] = 1 if float(op[0]) >= 0.5 else 0
            except (TypeError, ValueError):
                pass
    return out


async def fetch_prices_history(token_id: str, start_ts: int, end_ts: int,
                                fidelity: int = 1) -> Optional[list[dict]]:
    """CLOB /prices-history. Returns list of {'t': int, 'p': float} (UP-token prices).

    fidelity=1 (1-min) matches mining backfill — keeps SSOT alignment.
    """
    url = f"{CLOB_API}/prices-history"
    params = {"market": token_id, "startTs": start_ts, "endTs": end_ts, "fidelity": fidelity}
    try:
        r = await _client.get(url, params=params)
    except httpx.HTTPError as e:
        log.warning(f"prices-history {token_id[:10]}..: network err {e}")
        return None
    if r.status_code != 200:
        log.warning(f"prices-history {token_id[:10]}..: HTTP {r.status_code}")
        return None
    return r.json().get("history") or []


async def aclose():
    await _client.aclose()


# ---- self-test (live network call) -----------------------------------------
if __name__ == '__main__':
    import asyncio

    async def _selftest():
        # Pick a known closed candle: V2 era latest sample we have.
        cs = 1777872000   # 2026-05-04 05:20 UTC, settled (up_won=1)
        slug = slug_for(cs)
        ev = await fetch_event(slug)
        assert ev is not None, f"no event for {slug}"
        m = parse_market(ev)
        assert m is not None, "no market parsed"
        assert m['closed'] is True, "expected closed"
        assert m['up_won'] in (0, 1), m
        print(f"  ✓ event {slug}: market_id={m['market_id'][:14]}.. "
              f"up={m['up_token'][:8]}.. down={m['down_token'][:8]}.. "
              f"closed={m['closed']} up_won={m['up_won']}")

        ticks = await fetch_prices_history(m['up_token'], cs, cs + 300, fidelity=1)
        assert ticks, "no ticks"
        assert all('t' in t and 'p' in t for t in ticks), "tick schema"
        in_window = [t for t in ticks if cs <= t['t'] <= cs + 300]
        print(f"  ✓ ticks: total={len(ticks)} in_window=[{cs}, {cs+300}]={len(in_window)} "
              f"first={ticks[0]} last={ticks[-1]}")
        await aclose()

    asyncio.run(_selftest())
    print("pm_api: OK")
