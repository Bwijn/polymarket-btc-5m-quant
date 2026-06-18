"""ETL-mode EP backfill: PM /trades → compute p_intra in-flight → durable ep_panel.

Context: trades table dropped (79G→286MB, trades→EP 2026-06-17). Re-mine left the
  6/12→6/17 gap candles with NaN EP. This fetches their /trades, computes the EP
  panel in-flight (transform-in-flight → raw discarded, no db re-bloat), writes
  durably per-cid to ep_panel (≈0 crash loss).
Source: data-api /trades?market={cid}&limit=1000&offset={0,1000,2000,3000}&takerOnly=true
  4-page 4k-cap — probe-confirmed reaches cs+30 for hot markets, matches the original
  harvest method. (docs claim limit max 10000 but API caps a page at 1000 → real
  response is SSOT.) Rate: /trades = 200 req/10s; Semaphore(8) ≈ 16 req/s (80%).
Expected: ep_panel filled for gap cids. resume = cid NOT IN ep_panel. failures →
  _ingest_v3_failures. Idempotent re-run (done cids skipped). GFW: curl inherits
  HTTPS_PROXY (httpx async pool wedges through proxy; curl subprocess doesn't).
"""
import argparse
import asyncio
import json
import math
import os
import sqlite3
import sys
import time

sys.path.insert(0, "/home/polymarket_work")
from polybot.lib.compute.pmtrades import compute_pmtrades_features, _pmt_nan_record

DB = "/home/polymarket_work/db/pm_btc5m.db"
API = "https://data-api.polymarket.com"
EP_COLS = list(_pmt_nan_record().keys())
_INSERT_SQL = (f'INSERT OR REPLACE INTO ep_panel '
               f'({",".join(chr(34)+c+chr(34) for c in ["cid","cs",*EP_COLS,"fetched_at"])}) '
               f'VALUES ({",".join("?"*(len(EP_COLS)+3))})')


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--retries", type=int, default=3)
    return p.parse_args()


async def fetch_page(cid, offset, timeout, tries):
    """One /trades page via subprocess curl (inherits HTTPS_PROXY)."""
    url = f"{API}/trades?market={cid}&limit=1000&offset={offset}&takerOnly=true"
    for i in range(tries):
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-s", "--max-time", str(int(timeout)),
                "-H", "User-Agent: curl/8.5.0", url,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"curl exit={proc.returncode}: {err.decode()[:200]}")
            return json.loads(out)
        except Exception:
            if i == tries - 1:
                raise
            await asyncio.sleep(min(2 ** i, 4))


async def ingest_cid(ev, con, sem, stats, timeout, tries):
    """Fetch ≤4 pages, window-filter, compute EP, durable INSERT ep_panel."""
    cid, cs, up_tok, dn_tok = ev
    async with sem:
        try:
            rows = []
            for off in (0, 1000, 2000, 3000):
                tr = await fetch_page(cid, off, timeout, tries)
                for t in tr:                              # window [cs-300, cs+330], drop raw
                    ts = int(t["timestamp"])
                    if cs - 300 <= ts < cs + 330:
                        rows.append((ts, t["side"], float(t["price"]), float(t["size"]),
                                     t.get("asset"), t.get("proxyWallet")))
                if len(tr) < 1000:
                    break
            ep = compute_pmtrades_features(rows, cs, up_tok, dn_tok)   # 40 EP (NaN if no BUY)
            vals = [cid, cs] + [None if math.isnan(float(ep[c])) else float(ep[c])
                                for c in EP_COLS] + [int(time.time())]
            con.execute(_INSERT_SQL, vals)                # sync write, no await → no interleave
            con.commit()
            stats["done"] += 1
            stats["with_ep"] += 0 if vals[2 + EP_COLS.index("p_intra_60_up")] is None else 1
        except Exception as e:
            stats["failed"] += 1
            stats["last_err"] = f"{cid[:14]} {type(e).__name__}: {str(e)[:80]}"
            try:
                con.execute("INSERT INTO _ingest_v3_failures VALUES (?,?,?,?)",
                            (cid, int(time.time()), type(e).__name__, str(e)[:200]))
                con.commit()
            except Exception:
                pass


async def printer(stats, total):
    while stats["done"] + stats["failed"] < total:
        await asyncio.sleep(10)
        el = time.time() - stats["start"]
        rate = stats["done"] / el if el > 0 else 0
        eta = (total - stats["done"] - stats["failed"]) / rate / 60 if rate > 0 else 0
        msg = (f"  [{stats['done']:>5}/{total} | {stats['failed']} fail] "
               f"with_ep={stats['with_ep']:>5} rate={rate:.1f} cid/s ETA={eta:.0f}min")
        if stats["last_err"]:
            msg += f"  last_err={stats['last_err']}"
        print(msg, flush=True)


async def main():
    args = parse_args()
    if not (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")):
        raise RuntimeError("No HTTPS_PROXY — direct PM connect is GFW-blocked.")
    con = sqlite3.connect(DB, timeout=60, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA synchronous=NORMAL")

    gap = con.execute(                                   # resume: cid NOT IN ep_panel
        "SELECT cid, candle_start, up_token, down_token FROM events "
        "WHERE up_won IS NOT NULL AND cid NOT IN (SELECT cid FROM ep_panel) "
        "ORDER BY candle_start").fetchall()
    if args.limit:
        gap = gap[:args.limit]
    if not gap:
        print("nothing to do — ep_panel complete."); return
    print(f"EP backfill: {len(gap):,} gap cids, concurrency={args.concurrency}", flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    stats = {"done": 0, "failed": 0, "with_ep": 0, "last_err": "", "start": time.time()}
    pt = asyncio.create_task(printer(stats, len(gap)))
    await asyncio.gather(*[ingest_cid(ev, con, sem, stats, args.timeout, args.retries)
                           for ev in gap])
    pt.cancel()
    try:
        await pt
    except asyncio.CancelledError:
        pass
    print(f"\n=== DONE {(time.time()-stats['start'])/60:.1f} min ===")
    print(f"  done={stats['done']:,} failed={stats['failed']:,} with_ep={stats['with_ep']:,}")
    con.close()


if __name__ == "__main__":
    asyncio.run(main())
