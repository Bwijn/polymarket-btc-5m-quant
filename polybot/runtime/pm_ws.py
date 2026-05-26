"""WS book + trades cache for PM CLOB market channel.

Two caches:
  - BookCache: latest best_bid/best_ask per asset_id (from price_change / book events)
  - TradesCache: per-cid (market) rolling list of trades (from last_trade_price events)

Auto-reconnects on close (PM idle-timeout, GFW SSL cuts, network blips, etc.).
Re-subscribes when the active 5min candle rolls.

Why ws (not poll /book): paper trade's deliverable is *drift between backtest's
prices-history-based entry vs. real orderbook ask*. Polling /book adds 200ms
HTTP RTT — mistaking that latency for orderbook drift. ws gives 0-lag instant
best_ask at the trigger moment.

Why ws trades (not /trades API): PM /trades endpoint does NOT support
startTime/endTime filter (verified 2026-05-26), so historical window query
requires pagination + in-script filter. ws push delivers same trade records
with sub-second latency, naturally maintains rolling window.
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


class TradesCache:
    """Rolling per-cid trades list, populated from ws 'last_trade_price' events.

    Trade tuple: (ts_seconds, side, price, size, asset_id, proxy_wallet=None).
    proxy_wallet always None — ws event payload does not include it (only
    PM /trades pull API does). Affects pmt_wallets / pmt_whale features only;
    P-series strategies do not use them, so OK.

    Source: PM ws 'last_trade_price' event_type (Market Channel docs).
    Window: kept per cid, evicted when scanner unsubscribes that cid (on
    candle roll), aligned with BookCache eviction policy.
    """

    def __init__(self):
        self._trades: dict[str, list[tuple]] = {}   # cid → list of trade tuples

    def record(self, event: dict) -> None:
        cid = event.get('market')
        asset_id = event.get('asset_id')
        if not cid or not asset_id:
            return
        try:
            ts = int(int(event['timestamp']) / 1000)   # PM ms → s
            side = event['side']
            price = float(event['price'])
            size = float(event['size'])
        except (KeyError, TypeError, ValueError):
            return
        trade = (ts, side, price, size, asset_id, None)
        self._trades.setdefault(cid, []).append(trade)

    def get(self, cid: str) -> list[tuple]:
        """All cached trades for cid (compute_pmtrades_features filters by cs window itself)."""
        return self._trades.get(cid, [])

    def evict(self, cid: str) -> None:
        """Drop all trades for cid (called on candle-roll unsubscribe)."""
        self._trades.pop(cid, None)

    def gc(self, max_age_s: int = 7200) -> None:
        """Drop trades older than max_age_s seconds (time-based, decoupled from
        subscribe lifecycle). 2h default covers 2 candles' PRE+INTRA window comfortably."""
        cutoff = int(time.time()) - max_age_s
        for cid in list(self._trades.keys()):
            kept = [t for t in self._trades[cid] if t[0] >= cutoff]
            if kept:
                self._trades[cid] = kept
            else:
                del self._trades[cid]


class WsBookManager:
    """ONE long-lived ws connection. Reconnect only on network error.

    Sub list = (UP, DOWN) tokens of the *current* AND *next* 5min candle, kept
    fresh via PM ws updateSubscription protocol (operation=subscribe/unsubscribe
    on the live connection, no disconnect). On candle roll:
      - subscribe NEW next (new cs+300 market)
      - unsubscribe OLD candle that has expired (cs < current)
      - evict its BookCache entries (whitelist policy: cache mirrors active subs)

    Why long-lived: previous design closed+reconnected every 5min on candle roll,
    creating ~300-1500ms gap (2× HTTP resolve + 1× ws handshake) during which
    book_cache returned stale snapshot → et=0s triggers (R4/R6/R7) recorded
    systematically-biased entry_price_paper, polluting drift measurement.
    Per PM ws docs (/api-reference/wss/market.md updateSubscription schema), sub
    add/remove on live connection is supported zero-disconnect.
    """

    def __init__(self, cache: BookCache, trades_cache: 'TradesCache | None' = None):
        self.cache = cache
        self.trades_cache = trades_cache    # optional; if None, last_trade_price events skipped
        self._http = httpx.AsyncClient(timeout=8.0)
        # State for the current ws session. Cleared on disconnect.
        self._subbed_cs: set[int] = set()           # candle_starts currently subscribed
        self._cs_tokens: dict[int, tuple[str, str]] = {}  # cs → (up_token, dn_token)
        self._last_maintain_ts: int = 0             # throttle: don't HTTP-spam Gamma
        self._last_trades_gc_ts: int = 0            # trades_cache.gc() throttle

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

    async def _initial_subscribe(self, ws) -> bool:
        """Initial Subscribe with initial_dump=True. Returns False if no current
        candle resolvable (caller retries)."""
        cs = (int(time.time()) // 300) * 300
        cs_next = cs + 300
        cur = await self._resolve_tokens(cs)
        if cur is None:
            return False
        nxt = await self._resolve_tokens(cs_next)
        assets = list(cur)
        self._subbed_cs.add(cs)
        self._cs_tokens[cs] = cur
        if nxt is not None:
            assets.extend(nxt)
            self._subbed_cs.add(cs_next)
            self._cs_tokens[cs_next] = nxt
        await ws.send(json.dumps({
            "type": "market", "assets_ids": assets, "initial_dump": True,
        }))
        log.info(f"ws initial sub cs={cs}+next ({len(assets)} assets)")
        return True

    async def _maintain_subs(self, ws) -> None:
        """Keep sub list = {current_cs, next_cs}. Add new, drop expired, evict
        BookCache for evicted tokens (whitelist eviction = no cache leak)."""
        now_cs = (int(time.time()) // 300) * 300
        target = {now_cs, now_cs + 300}

        for cs in (now_cs, now_cs + 300):
            if cs in self._subbed_cs:
                continue
            tokens = await self._resolve_tokens(cs)
            if tokens is None:
                continue
            await ws.send(json.dumps({
                "operation": "subscribe", "assets_ids": list(tokens),
            }))
            self._subbed_cs.add(cs)
            self._cs_tokens[cs] = tokens
            log.info(f"ws +sub cs={cs}")

        for cs in list(self._subbed_cs):
            if cs in target:
                continue
            tokens = self._cs_tokens.pop(cs)
            self._subbed_cs.discard(cs)
            try:
                await ws.send(json.dumps({
                    "operation": "unsubscribe", "assets_ids": list(tokens),
                }))
            except Exception as e:
                log.warning(f"unsub cs={cs} send err: {e}")
            for tok in tokens:
                self.cache._cache.pop(tok, None)
            log.info(f"ws -sub cs={cs} (cache evicted)")

    async def _listen_and_maintain(self, ws) -> None:
        """Process incoming ws frames + maintain sub list per candle roll.
        Maintenance is triggered when the current/next bucket isn't in our sub
        set (i.e., candle just rolled), but throttled to ≤1/3s to avoid HTTP-
        spamming Gamma when next candle isn't yet created on PM."""
        async for msg in ws:
            now = int(time.time())
            now_cs = (now // 300) * 300
            stale = now_cs not in self._subbed_cs or (now_cs + 300) not in self._subbed_cs
            if stale and now - self._last_maintain_ts >= 3:
                self._last_maintain_ts = now
                await self._maintain_subs(ws)

            # Periodic trades_cache GC (every 5min, decoupled from subscribe)
            if self.trades_cache is not None and now - self._last_trades_gc_ts >= 300:
                self._last_trades_gc_ts = now
                self.trades_cache.gc()

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
                elif et == 'last_trade_price' and self.trades_cache is not None:
                    self.trades_cache.record(e)
                # ignore tick_size_change

    async def run_forever(self):
        log.info("ws book manager started (long-lived, updateSubscription on roll)")
        backoff_idx = 0
        while True:
            try:
                async with websockets.connect(WSS_URL, open_timeout=10, close_timeout=5) as ws:
                    if not await self._initial_subscribe(ws):
                        log.warning("initial subscribe failed (no current candle), retry 2s")
                        await asyncio.sleep(2)
                        continue
                    backoff_idx = 0
                    await self._listen_and_maintain(ws)
                    log.info("ws stream ended cleanly (PM closed?), reconnecting")
            except (websockets.ConnectionClosed,
                    asyncio.TimeoutError, OSError) as e:
                wait = RECONNECT_BACKOFF_S[min(backoff_idx, len(RECONNECT_BACKOFF_S) - 1)]
                log.warning(f"ws connect/listen err: {e.__class__.__name__}: {e} — retry in {wait}s")
                backoff_idx += 1
                await asyncio.sleep(wait)
            except Exception as e:
                log.exception(f"ws unexpected: {e}")
                await asyncio.sleep(2)
            finally:
                # State cleanup: next iteration will re-initial_subscribe from scratch
                self._subbed_cs.clear()
                self._cs_tokens.clear()
                self._last_maintain_ts = 0


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
