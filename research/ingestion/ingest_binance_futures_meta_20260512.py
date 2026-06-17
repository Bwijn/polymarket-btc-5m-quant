"""Ingest Binance USDM futures metadata (funding rate / open interest / long-short ratios).

Context: Spot klines already done. Crypto futures meta gives extra dimensions for
         cross-source signals: funding (rate of spot-futures basis), OI (positioning),
         top trader vs retail long/short (sentiment / whale vs noise). All
         documented by Binance Futures public API, no auth needed.
Source:  fapi.binance.com — fundingRate / openInterestHist / topLongShortAccountRatio
         / topLongShortPositionRatio / globalLongShortAccountRatio
Expected: 4 tables, each ~thousands rows over Feb 12 → today, total <50 MB.
"""
import sqlite3
import time
from datetime import datetime, timezone

import httpx

DB = '/home/polymarket_work/db/pm_btc5m.db'
FAPI = 'https://fapi.binance.com'

START_MS = int(datetime(2026, 2, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)


def create_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS binance_funding_rate (
            funding_ts_ms INTEGER PRIMARY KEY,
            funding_rate REAL,
            mark_price REAL,
            fetched_at INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS binance_open_interest_hist (
            ts_ms INTEGER PRIMARY KEY,
            sum_open_interest REAL,
            sum_open_interest_value REAL,
            fetched_at INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS binance_top_ls_account_ratio (
            ts_ms INTEGER PRIMARY KEY,
            long_account REAL, short_account REAL, long_short_ratio REAL,
            fetched_at INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS binance_top_ls_position_ratio (
            ts_ms INTEGER PRIMARY KEY,
            long_account REAL, short_account REAL, long_short_ratio REAL,
            fetched_at INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS binance_global_ls_account_ratio (
            ts_ms INTEGER PRIMARY KEY,
            long_account REAL, short_account REAL, long_short_ratio REAL,
            fetched_at INTEGER
        )
    """)
    con.commit()


def get_retry(cli, url, params, max_retry=5):
    last_exc = None
    for i in range(max_retry):
        try:
            r = cli.get(url, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, httpx.ConnectError, httpx.ReadTimeout) as e:
            last_exc = e
            time.sleep(min(2 ** i, 15))
    raise last_exc


def paginated_fetch(cli, path, base_params, table, parser, con, chunk_days=25, start_override_ms=None):
    """Generic paginator: walks startTime forward in chunk_days windows.
    Futures data endpoints (OI / longShortRatio) only retain ~25-30 days history.
    400 'startTime invalid' caught → skip that window and try the next."""
    NOW = int(time.time())
    end_ms = int(time.time() * 1000)
    chunk_ms = chunk_days * 86400 * 1000
    cursor_ms = start_override_ms if start_override_ms is not None else START_MS
    n_calls = 0
    while cursor_ms < end_ms:
        window_end = min(cursor_ms + chunk_ms, end_ms)
        params = dict(base_params)
        params.update({'startTime': cursor_ms, 'endTime': window_end, 'limit': 500})
        try:
            rows = get_retry(cli, f'{FAPI}{path}', params)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                print(f"    400 at startTime={cursor_ms}, skipping window")
                cursor_ms = window_end
                continue
            raise
        n_calls += 1
        if not rows:
            cursor_ms = window_end
            continue
        last_ts = 0
        for r in rows:
            try:
                values = parser(r, NOW)
                placeholders = ','.join('?' * len(values))
                con.execute(
                    f"INSERT OR IGNORE INTO {table} VALUES ({placeholders})",
                    values,
                )
                last_ts = max(last_ts, values[0])
            except (KeyError, ValueError, TypeError) as e:
                print(f"    parse err: {e}, row={r}")
        con.commit()
        # If we got less than limit, the chunk is exhausted; move to next window.
        if len(rows) < 500:
            cursor_ms = window_end
        elif last_ts <= cursor_ms:
            cursor_ms = window_end  # safety: prevent infinite loop on duplicate ts
        else:
            cursor_ms = last_ts + 1
        time.sleep(0.2)
    print(f"  {table}: {n_calls} calls, rows currently in db = "
          f"{con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]}")


def main():
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    cli = httpx.Client(timeout=30, http2=False)

    print('=' * 70); print('Phase 1: create tables'); print('=' * 70)
    create_tables(con)

    def safe_phase(label, fn):
        print(); print('=' * 70); print(label); print('=' * 70)
        try:
            fn()
        except Exception as e:
            print(f"  PHASE FAILED: {type(e).__name__}: {str(e)[:200]}")

    safe_phase('Phase 2: fundingRate (8h interval)', lambda: paginated_fetch(
        cli, '/fapi/v1/fundingRate', {'symbol': 'BTCUSDT'},
        'binance_funding_rate',
        lambda r, now: (int(r['fundingTime']), float(r['fundingRate']),
                        float(r.get('markPrice') or 0), now),
        con))

    # Futures data endpoints retain only ~25-30 days. longShortRatio is strict
    # about startTime within available range; OI tolerates. Use 20d for safety.
    HOUR_MS = 3600 * 1000
    last_30d_ms = ((int(time.time() * 1000) - 30 * 86400 * 1000) // HOUR_MS) * HOUR_MS
    last_20d_ms = ((int(time.time() * 1000) - 20 * 86400 * 1000) // HOUR_MS) * HOUR_MS

    safe_phase('Phase 3: openInterestHist (1h, 30d only)', lambda: paginated_fetch(
        cli, '/futures/data/openInterestHist',
        {'symbol': 'BTCUSDT', 'period': '1h'},
        'binance_open_interest_hist',
        lambda r, now: (int(r['timestamp']), float(r['sumOpenInterest']),
                        float(r['sumOpenInterestValue']), now),
        con, start_override_ms=last_30d_ms))

    safe_phase('Phase 4: topLongShortAccountRatio (1h, 30d only)', lambda: paginated_fetch(
        cli, '/futures/data/topLongShortAccountRatio',
        {'symbol': 'BTCUSDT', 'period': '1h'},
        'binance_top_ls_account_ratio',
        lambda r, now: (int(r['timestamp']), float(r['longAccount']),
                        float(r['shortAccount']), float(r['longShortRatio']), now),
        con, start_override_ms=last_20d_ms))

    safe_phase('Phase 5: topLongShortPositionRatio (1h, 20d only)', lambda: paginated_fetch(
        cli, '/futures/data/topLongShortPositionRatio',
        {'symbol': 'BTCUSDT', 'period': '1h'},
        'binance_top_ls_position_ratio',
        lambda r, now: (int(r['timestamp']), float(r['longAccount']),
                        float(r['shortAccount']), float(r['longShortRatio']), now),
        con, start_override_ms=last_20d_ms))

    safe_phase('Phase 6: globalLongShortAccountRatio (1h, 20d only)', lambda: paginated_fetch(
        cli, '/futures/data/globalLongShortAccountRatio',
        {'symbol': 'BTCUSDT', 'period': '1h'},
        'binance_global_ls_account_ratio',
        lambda r, now: (int(r['timestamp']), float(r['longAccount']),
                        float(r['shortAccount']), float(r['longShortRatio']), now),
        con, start_override_ms=last_20d_ms))

    print(); print('=' * 70); print('Verify'); print('=' * 70)
    for table in ('binance_funding_rate', 'binance_open_interest_hist',
                  'binance_top_ls_account_ratio', 'binance_top_ls_position_ratio',
                  'binance_global_ls_account_ratio'):
        ts_col = 'funding_ts_ms' if table == 'binance_funding_rate' else 'ts_ms'
        row = con.execute(f"""
            SELECT COUNT(*), datetime(MIN({ts_col})/1000, 'unixepoch'),
                   datetime(MAX({ts_col})/1000, 'unixepoch') FROM {table}
        """).fetchone()
        print(f"  {table}: n={row[0]}, utc=[{row[1]}, {row[2]}]")

    cli.close()
    con.close()


if __name__ == '__main__':
    main()
