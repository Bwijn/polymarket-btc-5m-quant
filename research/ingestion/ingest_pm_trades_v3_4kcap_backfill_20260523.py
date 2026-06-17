"""Backfill trades table with new 4000-cap pagination strategy (Solution B).

Context: V1/V2 ingest used limit=500 × 3 offsets = 1500-cap, missed early-candle
  trades for 98.6% of cids → bt entry-price estimate biased. Server real caps
  (probed 2026-05-23): limit≤1000 silent, offset≤3000 enforced → max reachable
  4000 trades/cid. Re-pull 4 pages per cid, INSERT OR IGNORE handles dedup on
  the existing 8-col composite PK.
Source: data-api /trades?market={cid}&limit=1000&offset={0,1000,2000,3000}
Expected: backfills ~2000-2500 NEW rows per cap_hit cid; 0 for cold cids.
  ~98 min single-threaded @ ~17 req/s (rate-limit ceiling 200/10s = 20 req/s).
  Idempotent + resume — re-running skips cids in _ingest_v3_progress.
Run: uv run python scratch/research/ingestion/ingest_pm_trades_v3_4kcap_backfill_20260523.py [--limit N]
"""
import argparse
import sqlite3
import sys
import time

import httpx

DB = "/home/polymarket_work/db/pm_btc5m.db"
API = "https://data-api.polymarket.com"
SLEEP_PER_CALL = 0.06   # 16.6 req/s sustained — comfortable under 200/10s


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None,
                   help="process only first N un-done cids (for test runs)")
    p.add_argument("--no-resume", action="store_true",
                   help="re-process cids already in _ingest_v3_progress")
    return p.parse_args()


def ensure_progress_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS _ingest_v3_progress (
        cid TEXT PRIMARY KEY,
        fetched_at INTEGER NOT NULL,
        n_new_rows INTEGER NOT NULL,
        n_calls INTEGER NOT NULL
    )""")
    con.commit()


def fetch_retry(cli, cid, offset, tries=5):
    for i in range(tries):
        try:
            r = cli.get(f"{API}/trades", params={
                "market": cid, "limit": 1000,
                "offset": offset, "takerOnly": "true",
            })
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 ** i)


def ingest_cid(con, cli, cid):
    n_new = n_calls = 0
    for off in (0, 1000, 2000, 3000):
        trades = fetch_retry(cli, cid, off)
        n_calls += 1
        for t in trades:
            try:
                cur = con.execute("""INSERT OR IGNORE INTO trades
                    (cid, ts, side, price, size, asset, proxy_wallet, tx_hash)
                    VALUES (?,?,?,?,?,?,?,?)""", (
                    t.get("conditionId"), int(t["timestamp"]), t["side"],
                    float(t["price"]), float(t["size"]),
                    t.get("asset"), t.get("proxyWallet"),
                    t.get("transactionHash"),
                ))
                if cur.rowcount > 0:
                    n_new += 1
            except (KeyError, ValueError, TypeError):
                continue
        # exhausted this cid (got less than full page = no more pages to ask for)
        if len(trades) < 1000:
            break
        time.sleep(SLEEP_PER_CALL)
    return n_new, n_calls


def main():
    args = parse_args()
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    ensure_progress_table(con)

    cids = [r[0] for r in con.execute(
        "SELECT cid FROM events ORDER BY candle_start").fetchall()]
    total_universe = len(cids)
    if not args.no_resume:
        done = {r[0] for r in con.execute(
            "SELECT cid FROM _ingest_v3_progress").fetchall()}
        cids = [c for c in cids if c not in done]
        print(f"resume mode: {len(done):,} done, {len(cids):,} remaining "
              f"(of {total_universe:,} total)", flush=True)
    if args.limit is not None:
        cids = cids[: args.limit]
        print(f"--limit: only processing {len(cids)} cid(s)", flush=True)

    if not cids:
        print("nothing to do."); return

    cli = httpx.Client(timeout=20, headers={"User-Agent": "curl/8.5.0"})
    t0 = time.time()
    sum_new = sum_calls = 0

    for i, cid in enumerate(cids, 1):
        try:
            n_new, n_calls = ingest_cid(con, cli, cid)
        except Exception as e:
            print(f"  [{i}/{len(cids)}] {cid[:14]}.. FAILED "
                  f"{type(e).__name__}: {str(e)[:80]}", flush=True)
            continue
        con.execute("INSERT OR REPLACE INTO _ingest_v3_progress VALUES (?,?,?,?)",
                    (cid, int(time.time()), n_new, n_calls))
        con.commit()
        sum_new += n_new; sum_calls += n_calls
        if i % 50 == 0 or i == len(cids) or i <= 5:
            elapsed = time.time() - t0
            rate_cid = i / elapsed
            eta_min = (len(cids) - i) / rate_cid / 60 if rate_cid > 0 else 0
            print(f"  [{i:>5}/{len(cids):<5}] {cid[:14]}.. "
                  f"new={n_new:>4} calls={n_calls}  "
                  f"sum_new={sum_new:>8,} sum_calls={sum_calls:>7,}  "
                  f"rate={rate_cid:.1f} cid/s  ETA={eta_min:.0f}min", flush=True)

    elapsed = time.time() - t0
    print(f"\n=== DONE in {elapsed/60:.1f} min ===")
    print(f"  cids processed: {len(cids):,}")
    print(f"  total NEW rows: {sum_new:,}")
    print(f"  total API calls: {sum_calls:,}")
    print(f"  avg new/cid: {sum_new/max(len(cids),1):.0f}")
    print(f"  avg calls/cid: {sum_calls/max(len(cids),1):.2f}")


if __name__ == "__main__":
    main()
