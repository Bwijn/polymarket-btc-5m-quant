"""WS book cache for PM CLOB market channel.

Maintains best_bid/best_ask per asset_id from price_change events. Initial
book snapshots seed the cache. Auto-reconnects on close (PM idle-timeout, GFW
SSL cuts, network blips, etc.). Re-subscribes when the active 5min candle
rolls.

Why ws (not poll /book): paper trade's deliverable is *drift between backtest's
prices-history-based entry vs. real orderbook ask*. Polling /book adds 200ms
HTTP RTT — mistaking that latency for orderbook drift. ws gives 0-lag instant
best_ask at the trigger moment.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from typing import Optional

import httpx
import websockets

from polybot.runtime.config import GAMMA_API, ASSET_PREFIX

log = logging.getLogger("polybot.pm_ws")

WSS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
RECONNECT_BACKOFF_S = (1, 2, 4, 8, 8, 8)   # capped exponential backoff


class BookCache:
    """Latest best_bid / best_ask per asset_id (best level only, not full book).

    Sources of update (in order of frequency):
      - price_change events: each carries best_bid/best_ask fields directly,
                              no book reconstruction needed.
      - book events:         full snapshot. We extract bids[-1].price and
                              asks[0].price (PM uses ascending order in both
                              arrays, so best_bid=last bid, best_ask=first ask).
    """

    def __init__(self):
        self._cache: dict[str, dict] = {}

    def update_from_book(self, e: dict) -> None:
        bids, asks = e.get('bids') or [], e.get('asks') or []
        bb = float(bids[-1]['price']) if bids else None
        ba = float(asks[0]['price'])  if asks else None
        aid = e.get('asset_id')
        if not aid:
            return
        self._cache[aid] = {
            'best_bid': bb, 'best_ask': ba,
            'ts_ms': int(e.get('timestamp', '0') or 0),
            'source': 'book',
        }

    def update_from_price_change(self, e: dict) -> None:
        for c in e.get('price_changes', []):
            aid = c.get('asset_id')
            if not aid:
                continue
            try:
                bb = float(c['best_bid']) if c.get('best_bid') else None
                ba = float(c['best_ask']) if c.get('best_ask') else None
            except (TypeError, ValueError):
                continue
            self._cache[aid] = {
                'best_bid': bb, 'best_ask': ba,
                'ts_ms': int(e.get('timestamp', '0') or 0),
                'source': 'price_change',
            }

    def snapshot(self, asset_id: str) -> Optional[dict]:
        return self._cache.get(asset_id)


class WsBookManager:
    """One ws connection. Reconnects on close. Resubscribes when candle rolls.

    Sub list = (UP, DOWN) tokens of the *current* AND *next* 5min candle.
    Pre-subscribing next gives ~5min lead time for book_cache to fill before
    that candle becomes current — critical for entry_offset_s=0 strategies
    (R4/R6/R7) which trigger at exactly cs and need a warm book_cache snapshot.
    Without pre-sub, ws connect+resolve+first-message latency causes
    entry_price_paper=NULL for ~80% of et=0s triggers.
    """

    def __init__(self, cache: BookCache):
        self.cache = cache
        self._http = httpx.AsyncClient(timeout=8.0)

    async def aclose(self):
        await self._http.aclose()

    async def _resolve_tokens(self, cs: int) -> Optional[tuple[str, str]]:
        slug = f"{ASSET_PREFIX}{cs}"
        try:
            r = await self._http.get(f"{GAMMA_API}/events/slug/{slug}")
        except httpx.HTTPError as e:
            log.warning(f"resolve tokens cs={cs}: {e}")
            return None
        if r.status_code != 200:
            return None
        try:
            ev = r.json()
            m = ev["markets"][0]
            tokens = json.loads(m["clobTokenIds"])
            return (tokens[0], tokens[1])
        except (KeyError, IndexError, json.JSONDecodeError, TypeError) as e:
            log.warning(f"parse tokens cs={cs}: {e}")
            return None

    async def _listen(self, ws, cs_active: int) -> str:
        """Read frames until candle rolls or connection breaks. Return reason."""
        async for msg in ws:
            if (int(time.time()) // 300) * 300 != cs_active:
                return "candle_rolled"
            try:
                payload = json.loads(msg)
            except json.JSONDecodeError:
                continue
            events = payload if isinstance(payload, list) else [payload]
            for e in events:
                et = e.get('event_type')
                if et == 'price_change':
                    self.cache.update_from_price_change(e)
                elif et == 'book':
                    self.cache.update_from_book(e)
                # ignore last_trade_price / tick_size_change
        return "stream_ended"

    async def run_forever(self):
        log.info("ws book manager started")
        backoff_idx = 0
        last_cs = None
        while True:
            cs = (int(time.time()) // 300) * 300
            cs_next = cs + 300
            cur_tokens  = await self._resolve_tokens(cs)
            if cur_tokens is None:
                await asyncio.sleep(2)
                continue
            # Pre-sub next: tolerated if not yet created on PM (returns None → skip).
            next_tokens = await self._resolve_tokens(cs_next)

            assets = list(cur_tokens)
            if next_tokens is not None:
                assets.extend(next_tokens)

            try:
                if cs != last_cs:
                    pre = f" +next={next_tokens[0][:8]}../{next_tokens[1][:8]}.." if next_tokens else " (next not yet on PM)"
                    log.info(f"ws sub cs={cs} cur={cur_tokens[0][:8]}../{cur_tokens[1][:8]}..{pre}")
                    last_cs = cs

                async with websockets.connect(WSS_URL, open_timeout=10, close_timeout=5) as ws:
                    await ws.send(json.dumps({
                        "type": "market",
                        "assets_ids": assets,
                        "initial_dump": True,
                    }))
                    backoff_idx = 0  # successful connect resets backoff
                    reason = await self._listen(ws, cs)
                    log.info(f"ws closed cs={cs} reason={reason}")
            except (websockets.ConnectionClosed,
                    asyncio.TimeoutError, OSError) as e:
                wait = RECONNECT_BACKOFF_S[min(backoff_idx, len(RECONNECT_BACKOFF_S) - 1)]
                log.warning(f"ws connect/listen err: {e.__class__.__name__}: {e} — retry in {wait}s")
                backoff_idx += 1
                await asyncio.sleep(wait)
            except Exception as e:
                log.exception(f"ws unexpected: {e}")
                await asyncio.sleep(2)


# ---- self-test (live, 10s) --------------------------------------------------
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

    async def _selftest():
        cache = BookCache()
        mgr = WsBookManager(cache)
        task = asyncio.create_task(mgr.run_forever())
        # let it run 10s, then dump cache
        await asyncio.sleep(10)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await mgr.aclose()
        print("=" * 60)
        print(f"BookCache after 10s, {len(cache._cache)} assets seen")
        for aid, snap in cache._cache.items():
            print(f"  {aid[:12]}.. best_bid={snap['best_bid']} best_ask={snap['best_ask']} "
                  f"src={snap['source']} ts={snap['ts_ms']}")

    asyncio.run(_selftest())
