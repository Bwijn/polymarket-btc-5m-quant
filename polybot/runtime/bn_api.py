"""Binance public klines fetcher. Read-only, no auth.

Used by scanner to pull 1-min BTCUSDT klines covering [cs-3600, cs] for
compute_bn_features (paper-time bn_* atoms).

Rate limit: /api/v3/klines weight=1-2; we issue ~1 request per 5min event,
well under the 1200 weight/min budget.

API contract (https://developers.binance.com/docs/binance-spot-api-docs/rest-api):
    GET https://api.binance.com/api/v3/klines
    symbol=BTCUSDT, interval=1m, startTime=<ms>, endTime=<ms>, limit≤1000
    returns: [[open_ts_ms, open, high, low, close, volume, close_ts_ms, quote_volume,
              num_trades, taker_buy_volume, taker_buy_quote_volume, ignore], ...]
"""
from __future__ import annotations
import httpx
import pandas as pd

from polybot.runtime.coalesce import coalesce

BN_KLINES_URL = "https://api.binance.com/api/v3/klines"


@coalesce(ttl_s=15.0)   # [cs-3600, cs] window is in the past = immutable; herd shares one fetch
async def fetch_klines(start_s: int, end_s: int, symbol: str = "BTCUSDT") -> pd.DataFrame:
    """Fetch 1-min klines covering [start_s, end_s] inclusive. Returns DataFrame
    indexed by ts (unix seconds at minute boundary), columns matching mining
    binance_klines table (open, high, low, close, volume, quote_volume,
    taker_buy_volume, n_trades).

    Boundary invariant: /klines endTime is inclusive on OPEN time. When end_s is
    a minute boundary the LAST row is the still-forming candle (open==end_s,
    partial/unfinalized) — NOT a closed bar. Consumers MUST slice `index < cs`
    before use (compute_bn_features does); never `.iloc[-1]` on the raw frame.

    Returns empty DataFrame on HTTP error (caller handles missing data via NaN).
    """
    params = {
        "symbol": symbol,
        "interval": "1m",
        "startTime": start_s * 1000,
        "endTime":   end_s   * 1000,
        "limit":     1000,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(BN_KLINES_URL, params=params)
            r.raise_for_status()
            rows = r.json()
        except (httpx.HTTPError, ValueError):
            return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        'open_ts_ms', 'open', 'high', 'low', 'close', 'volume', 'close_ts_ms',
        'quote_volume', 'n_trades', 'taker_buy_volume', 'taker_buy_quote_volume', 'ignore'
    ])
    df['ts'] = (df['open_ts_ms'] // 1000).astype('int64')
    for c in ('open', 'high', 'low', 'close', 'volume', 'quote_volume', 'taker_buy_volume'):
        df[c] = df[c].astype(float)
    df['n_trades'] = df['n_trades'].astype('int64')
    df = df[['ts', 'open', 'high', 'low', 'close', 'volume', 'quote_volume',
             'taker_buy_volume', 'n_trades']]
    df.set_index('ts', inplace=True)
    return df
