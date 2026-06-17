"""Ingest Binance BTCUSDT 1min klines into pm_btc5m.db for new mining cycle.

Context: Old 2378 factors built on PM price-only data are spent. New cycle needs
         cross-source signal — Binance is the BTC underlying (标的) that PM market
         bets on. 1min granularity gives 5 ticks per PM 5min candle, enough for
         intra-candle momentum / realized vol / volume profile features.
Source:  GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m
         Rate: weight 5 per limit=1000 call, limit 6000/min → far under cap.
Expected: binance_klines table populated cs_min=2026-02-12 → today,
          ~127k rows, ~18 MB. Schema: open_ts_ms PK + OHLCV + taker_buy_volume
          (= main 'order flow' feature, fraction of volume that was aggressive buy).
"""
import sqlite3
import time
from datetime import datetime, timezone

import httpx

DB = '/home/polymarket_work/db/pm_btc5m.db'
BINANCE = 'https://api.binance.com'

# V1 era starts Feb 12 (per existing events table). Cover full V1+V2 span.
START_MS = int(datetime(2026, 2, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)


def create_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS binance_klines (
            open_ts_ms INTEGER PRIMARY KEY,
            open REAL, high REAL, low REAL, close REAL,
            volume REAL, quote_volume REAL, n_trades INTEGER,
            taker_buy_volume REAL, taker_buy_quote_volume REAL,
            fetched_at INTEGER
        )
    """)
    con.commit()


def get_retry(cli, url, params, max_retry=5):
    last_exc = None
    for i in range(max_retry):
        try:
            r = cli.get(url, params=params)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, httpx.ConnectError, httpx.ReadTimeout) as e:
            last_exc = e
            time.sleep(min(2 ** i, 15))
    raise last_exc


def fetch_range(con, cli, start_ms, end_ms):
    NOW = int(time.time())
    cursor = start_ms
    total_inserted = 0
    total_skipped = 0
    n_calls = 0
    t_start = time.time()

    while cursor < end_ms:
        klines = get_retry(cli, f'{BINANCE}/api/v3/klines', params={
            'symbol': 'BTCUSDT',
            'interval': '1m',
            'startTime': cursor,
            'endTime': end_ms,
            'limit': 1000,
        })
        n_calls += 1
        if not klines:
            break
        inserted = skipped = 0
        for k in klines:
            cur = con.execute("""
                INSERT OR IGNORE INTO binance_klines
                (open_ts_ms, open, high, low, close, volume, quote_volume,
                 n_trades, taker_buy_volume, taker_buy_quote_volume, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]),
                float(k[5]), float(k[7]), int(k[8]),
                float(k[9]), float(k[10]), NOW,
            ))
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        con.commit()
        total_inserted += inserted
        total_skipped += skipped
        last_open_ms = int(klines[-1][0])
        cursor = last_open_ms + 60_000  # next minute
        if n_calls % 10 == 0:
            elapsed = time.time() - t_start
            print(f"  call={n_calls}, inserted={total_inserted}, skipped={total_skipped}, "
                  f"cursor={datetime.fromtimestamp(cursor/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}, "
                  f"elapsed={elapsed:.0f}s", flush=True)
        # safety pacing: 0.1s/call → 10 req/s, far under 6000/min limit
        time.sleep(0.1)
    print(f"  DONE. inserted={total_inserted}, skipped={total_skipped}, calls={n_calls}, "
          f"elapsed={time.time()-t_start:.0f}s")


def main():
    con = sqlite3.connect(DB)
    cli = httpx.Client(timeout=30, http2=False)
    end_ms = int(time.time() * 1000)

    print('=' * 70); print('Phase 1: create binance_klines table'); print('=' * 70)
    create_table(con)

    # Auto-detect start: last open_ts_ms + 60s, else fall back to START_MS
    row = con.execute("SELECT MAX(open_ts_ms) FROM binance_klines").fetchone()
    last_ms = row[0] if row and row[0] else None
    start_ms = (last_ms + 60_000) if last_ms else START_MS

    print(); print('=' * 70)
    print(f"Phase 2: fetch klines {datetime.fromtimestamp(start_ms/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} "
          f"→ {datetime.fromtimestamp(end_ms/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")
    print('=' * 70)
    if start_ms >= end_ms:
        print("  klines up-to-date; skip")
    else:
        fetch_range(con, cli, start_ms, end_ms)

    print(); print('=' * 70); print('Verify'); print('=' * 70)
    row = con.execute("""
        SELECT COUNT(*) AS n,
               MIN(open_ts_ms) AS ms_min, MAX(open_ts_ms) AS ms_max,
               datetime(MIN(open_ts_ms)/1000, 'unixepoch') AS utc_min,
               datetime(MAX(open_ts_ms)/1000, 'unixepoch') AS utc_max
        FROM binance_klines
    """).fetchone()
    print(f"  binance_klines: n={row[0]}, ms=[{row[1]},{row[2]}], utc=[{row[3]}, {row[4]}]")

    cli.close()
    con.close()


if __name__ == '__main__':
    main()
