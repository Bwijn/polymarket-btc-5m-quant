"""Re-ingest Binance OI at 5m granularity → binance_open_interest_hist_5m.

Context: 1h OI in a per-5m-candle bt = within-hour pseudo-replication (signal const
  across 12 candles → effN inflated ~7×, mirage). Fix = 5m SAMPLING (per-candle update)
  keeping meaningful windows (oi_chg_pct_1h/4h). Probe confirmed 5m retention ~30d.
Source: fapi.binance.com /futures/data/openInterestHist period=5m (retains ~30d).
Expected: new table binance_open_interest_hist_5m (ts_ms PK + sum_open_interest BTC +
  sum_oi_value USDT). Additive — 1h table untouched (has older Apr-May history).
"""
from __future__ import annotations
import sqlite3
import time

import httpx

DB = '/home/polymarket_work/db/pm_btc5m.db'
FAPI = 'https://fapi.binance.com'
PATH = '/futures/data/openInterestHist'
PERIOD_MS = 5 * 60 * 1000
CHUNK = 500                                  # max limit per request → 500*5min = 41.6h/call
SPAN_DAYS = 29                               # within ~30d retention floor (probe: -31d → 400)


def get_retry(cli, params, tries=5):
    for k in range(tries):
        try:
            r = cli.get(f'{FAPI}{PATH}', params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:   # startTime beyond retention → no data
                return []
            time.sleep(1.0 + k)
        except httpx.HTTPError:
            time.sleep(1.0 + k)
    raise RuntimeError(f'get_retry exhausted: {params}')


def main():
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("""CREATE TABLE IF NOT EXISTS binance_open_interest_hist_5m (
        ts_ms INTEGER PRIMARY KEY,
        sum_open_interest REAL,
        sum_oi_value REAL,
        ingested_at INTEGER)""")
    cli = httpx.Client(http2=False)

    now_ms = int(time.time() * 1000)
    cursor = now_ms - SPAN_DAYS * 86400 * 1000
    chunk_ms = CHUNK * PERIOD_MS
    now_s = int(time.time())
    n_calls = n_rows = 0
    while cursor < now_ms:
        win_end = min(cursor + chunk_ms, now_ms)
        rows = get_retry(cli, {'symbol': 'BTCUSDT', 'period': '5m',
                               'startTime': cursor, 'endTime': win_end, 'limit': CHUNK})
        n_calls += 1
        if not rows:
            cursor = win_end
            continue
        for r in rows:
            con.execute("INSERT OR IGNORE INTO binance_open_interest_hist_5m VALUES (?,?,?,?)",
                        (int(r['timestamp']), float(r['sumOpenInterest']),
                         float(r['sumOpenInterestValue']), now_s))
        con.commit()
        n_rows += len(rows)
        last_ts = rows[-1]['timestamp']
        cursor = last_ts + 1 if last_ts > cursor else win_end
        time.sleep(0.2)

    total = con.execute("SELECT COUNT(*), MIN(ts_ms)/1000, MAX(ts_ms)/1000 "
                        "FROM binance_open_interest_hist_5m").fetchone()
    con.close()
    import datetime as dt
    lo = dt.datetime.fromtimestamp(total[1], dt.timezone.utc)
    hi = dt.datetime.fromtimestamp(total[2], dt.timezone.utc)
    print(f"calls={n_calls} fetched={n_rows} | table now {total[0]} rows "
          f"{lo:%Y-%m-%d %H:%M} → {hi:%Y-%m-%d %H:%M} UTC")


if __name__ == '__main__':
    main()
