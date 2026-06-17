"""Extend events to today — incremental, idempotent.

Discovers new bitcoin 5m updown markets (db-max candle_start → now) and fills
raw_event for any row missing it. Idempotent: INSERT OR IGNORE (PK=cid).
"""
import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta

import httpx

DB = '/home/polymarket_work/db/pm_btc5m.db'
GAMMA = "https://gamma-api.polymarket.com"


def get_retry(cli, url, params=None, max_retry=5):
    """httpx GET with exponential backoff retry."""
    last_exc = None
    for i in range(max_retry):
        try:
            return cli.get(url, params=params)
        except httpx.HTTPError as e:
            last_exc = e
            time.sleep(min(2 ** i, 15))
    raise last_exc


def discover_v2_events_window(con, cli, start_dt, end_dt):
    """Discover bitcoin 5m updown markets in [start_dt, end_dt) → INSERT OR IGNORE events.
    Window is db-max-cursor driven (caller), not a hardcoded cutoff."""
    r = get_retry(cli, f"{GAMMA}/tags/slug/bitcoin")
    r.raise_for_status()
    tag_id = r.json()['id']
    print(f"  bitcoin tag_id = {tag_id}, window {start_dt.date()} → {end_dt.date()}")

    NOW = int(time.time())
    total_inserted = total_skipped = 0

    n_days = (end_dt - start_dt).days
    for day_n in range(n_days):
        d_start = start_dt + timedelta(days=day_n)
        d_end = d_start + timedelta(days=1)
        day_inserted = day_skipped = 0
        offset = 0
        while True:
            r = get_retry(cli, f"{GAMMA}/events", params={
                'tag_id': tag_id,
                # Filter on endDate (structural: = candle_start + 5min), NOT startDate.
                # startDate = listing time, ~24h ahead but jittery & non-guaranteed by PM,
                # so a candle_start-derived cursor maps cleanly to an endDate window but not
                # a startDate one — startDate filtering silently dropped the seam day every run.
                'end_date_min': d_start.isoformat().replace('+00:00', 'Z'),
                'end_date_max': d_end.isoformat().replace('+00:00', 'Z'),
                'closed': True, 'limit': 500, 'offset': offset,
            })
            r.raise_for_status()
            events = r.json()
            if not events:
                break
            btc_5m = [e for e in events if e.get('slug', '').startswith('btc-updown-5m-')]
            for e in btc_5m:
                slug = e['slug']
                cs = int(slug.rsplit('-', 1)[-1])
                if cs % 300 != 0:
                    continue
                ce = cs + 300
                markets = e.get('markets', [])
                if not markets:
                    continue
                m = markets[0]
                cid = m['conditionId']
                tokens = json.loads(m['clobTokenIds']) if isinstance(m.get('clobTokenIds'), str) else m.get('clobTokenIds', [])
                if len(tokens) < 2:
                    continue
                up_token, down_token = tokens[0], tokens[1]
                op = m.get('outcomePrices', '[]')
                if isinstance(op, str):
                    op = json.loads(op)
                if len(op) >= 2 and e.get('closed'):
                    up_won = 1 if float(op[0]) >= 0.5 else 0
                else:
                    up_won = None
                raw_event = json.dumps(e, separators=(',', ':'))
                cur = con.execute("""
                    -- era='V2': cursor is forward-only past the 4/28 cutover, so all
                    -- discovered rows are V2. Written (not branched on) to keep the
                    -- retained era column correct vs its schema DEFAULT 'V1'.
                    INSERT OR IGNORE INTO events
                    (cid, slug, candle_start, candle_end, up_token, down_token, up_won, fetched_at, era, raw_event)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'V2', ?)
                """, (cid, slug, cs, ce, up_token, down_token, up_won, NOW, raw_event))
                if cur.rowcount > 0:
                    day_inserted += 1
                else:
                    day_skipped += 1
            # 2026-05-24 fix: Gamma silently caps limit (~100 actual), so len<500 was hit on
            # first page → exited after ~5h of data per day. Use actual length, break only on
            # empty response.
            if not events:
                break
            offset += len(events)
        con.commit()
        total_inserted += day_inserted
        total_skipped += day_skipped
        print(f"  {d_start.date()}: inserted={day_inserted}, skipped={day_skipped}")
    print(f"  total: inserted={total_inserted}, skipped={total_skipped}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force-from', type=str, default=None,
                    help='Force start date (YYYY-MM-DD UTC), overrides auto-detect. '
                         'Use when re-discovering historical days (e.g., after pagination bug fix).')
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    cli = httpx.Client(timeout=60, http2=False)

    today_utc = datetime.now(timezone.utc).date()
    if args.force_from:
        start_dt = datetime.fromisoformat(args.force_from).replace(tzinfo=timezone.utc)
        print(f"⚠️  --force-from override: start={args.force_from}, overrides auto-detect")
    else:
        # auto-detect: last candle_start + 5min, fall back to May 4
        row = con.execute("SELECT MAX(candle_start) FROM events").fetchone()
        last_cs = row[0] if row and row[0] else None
        if last_cs:
            start_dt = datetime.fromtimestamp(last_cs + 300, tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start_dt = datetime(2026, 5, 4, tzinfo=timezone.utc)
    end_dt   = datetime.combine(today_utc, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
    if start_dt >= end_dt:
        print(f"events up-to-date (today={today_utc}); discovery window empty (no-op)")


    print("=" * 70); print(f"Phase 1: discover events {start_dt.date()} → {end_dt.date()}"); print("=" * 70)
    discover_v2_events_window(con, cli, start_dt, end_dt)

    print(); print("=" * 70); print("Verify"); print("=" * 70)
    for row in con.execute("""
        SELECT era, COUNT(*) AS n,
               MIN(candle_start) AS cs_min, MAX(candle_start) AS cs_max,
               datetime(MIN(candle_start), 'unixepoch') AS utc_min,
               datetime(MAX(candle_start), 'unixepoch') AS utc_max
        FROM events GROUP BY era ORDER BY era
    """).fetchall():
        print(f"  era={row[0]} n={row[1]} cs=[{row[2]},{row[3]}] utc=[{row[4]},{row[5]}]")

    cli.close()
    con.close()


if __name__ == '__main__':
    main()
