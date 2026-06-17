"""Async 8-concurrent backfill of trades table with 4000-cap pagination.

Context: Sync version of this script (sibling file w/o _async) projected ~17h
  runtime due to ~0.5s API RTT × 4 calls × 24,884 cids serially. PM rate-limit
  is 200 req / 10s = 20 req/s; single-thread used only ~12%. Async with
  Semaphore(8) → ~16 req/s = 80% utilization, ETA ~1.5-2h.
Source: data-api /trades?market={cid}&limit=1000&offset={0,1000,2000,3000}
Shared with sync version: same trades table (8-col composite PK + INSERT OR
  IGNORE for dedup) and same _ingest_v3_progress table for resume — sync's
  already-done cids (110 from earlier test) are respected.
Run: uv run python scratch/research/ingestion/ingest_pm_trades_v3_4kcap_backfill_async_20260523.py [--limit N] [--concurrency N]
"""
import argparse
import asyncio
import json
import os
import sqlite3
import time

DB = "/home/polymarket_work/db/pm_btc5m.db"
API = "https://data-api.polymarket.com"


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--no-resume", action="store_true")
    return p.parse_args()


def ensure_progress_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS _ingest_v3_progress (
        cid TEXT PRIMARY KEY,
        fetched_at INTEGER NOT NULL,
        n_new_rows INTEGER NOT NULL,
        n_calls INTEGER NOT NULL
    )""")
    # multi-row per cid: one entry per failed attempt across rounds.
    # query "cid w/ failures >= 3" = permanent-failure shortlist.
    con.execute("""CREATE TABLE IF NOT EXISTS _ingest_v3_failures (
        cid TEXT NOT NULL,
        attempted_at INTEGER NOT NULL,
        error_type TEXT,
        error_msg TEXT
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_v3_failures_cid
        ON _ingest_v3_failures(cid)""")
    con.commit()


async def fetch_retry(cid, offset, timeout=15, tries=3):
    """Subprocess curl per call — bypasses httpx async pool wedging seen with
    proxy CONNECT tunnels under sustained load. curl inherits HTTPS_PROXY env
    automatically. Each subprocess opens fresh TCP+TLS through proxy → no
    shared pool state that can deadlock."""
    url = (f"{API}/trades?market={cid}&limit=1000"
           f"&offset={offset}&takerOnly=true")
    for i in range(tries):
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-s", "--max-time", str(int(timeout)),
                "-H", "User-Agent: curl/8.5.0",
                url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"curl exit={proc.returncode}: "
                                   f"{stderr.decode()[:200]}")
            return json.loads(stdout)
        except Exception:
            if i == tries - 1:
                raise
            await asyncio.sleep(min(2 ** i, 4))


async def ingest_cid(cid, con, sem, stats, timeout, tries):
    """Fetch 4 pages for one cid, write to trades, mark done in progress."""
    async with sem:
        n_new = n_calls = 0
        try:
            for off in (0, 1000, 2000, 3000):
                trades = await fetch_retry(cid, off, timeout=timeout, tries=tries)
                n_calls += 1
                # SQLite writes are sync, fast (~1-5ms), and inside the event
                # loop with no awaits → no risk of interleaved writes corrupting
                # state. No asyncio.Lock needed.
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
                if len(trades) < 1000:
                    break
            con.execute("INSERT OR REPLACE INTO _ingest_v3_progress VALUES (?,?,?,?)",
                        (cid, int(time.time()), n_new, n_calls))
            con.commit()
            stats["done"] += 1
            stats["new"] += n_new
            stats["calls"] += n_calls
        except Exception as e:
            stats["failed"] += 1
            stats["last_err"] = f"{cid[:14]} {type(e).__name__}: {str(e)[:80]}"
            try:
                con.execute("INSERT INTO _ingest_v3_failures VALUES (?,?,?,?)",
                            (cid, int(time.time()),
                             type(e).__name__, str(e)[:200]))
                con.commit()
            except Exception:
                pass   # logging shouldn't kill the loop


async def printer(stats, total):
    """Periodic stdout — every 10s, until done."""
    while stats["done"] + stats["failed"] < total:
        await asyncio.sleep(10)
        elapsed = time.time() - stats["start"]
        rate = stats["done"] / elapsed if elapsed > 0 else 0
        remaining = total - stats["done"] - stats["failed"]
        eta_min = remaining / rate / 60 if rate > 0 else 0
        msg = (f"  [{stats['done']:>5}/{total} done | {stats['failed']} failed] "
               f"new={stats['new']:>8,} calls={stats['calls']:>7,} "
               f"rate={rate:.1f} cid/s ETA={eta_min:.0f}min")
        if stats["last_err"]:
            msg += f"  last_err={stats['last_err']}"
        print(msg, flush=True)


async def main():
    args = parse_args()
    con = sqlite3.connect(DB, timeout=60, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA synchronous=NORMAL")
    ensure_progress_table(con)

    cids = [r[0] for r in con.execute(
        "SELECT cid FROM events ORDER BY candle_start").fetchall()]
    total = len(cids)
    if not args.no_resume:
        done = {r[0] for r in con.execute(
            "SELECT cid FROM _ingest_v3_progress").fetchall()}
        cids = [c for c in cids if c not in done]
        print(f"resume mode: {len(done):,} done, {len(cids):,} remaining "
              f"(of {total:,} total)", flush=True)
    if args.limit is not None:
        cids = cids[: args.limit]
        print(f"--limit: only processing {len(cids)} cid(s)", flush=True)
    if not cids:
        print("nothing to do."); return

    sem = asyncio.Semaphore(args.concurrency)
    stats = {"done": 0, "failed": 0, "new": 0, "calls": 0, "last_err": "",
             "start": time.time()}

    print(f"starting async backfill: {len(cids):,} cids, "
          f"concurrency={args.concurrency}", flush=True)

    # GFW blocks direct connection to PM (verified via curl probe). curl auto
    # inherits HTTPS_PROXY env. We previously tried httpx async client through
    # proxy; it wedged the connection pool after some sustained load (PoolTimeout
    # cascade). 50-parallel curl through the same proxy succeeded 100%, proving
    # the issue is httpx-specific, not proxy. Subprocess curl bypasses any
    # shared client pool state.
    proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))
    if not proxy:
        raise RuntimeError("No HTTPS_PROXY env — direct connect to PM is GFW-blocked.")
    print(f"using proxy (via curl env): {proxy}", flush=True)

    printer_task = asyncio.create_task(printer(stats, len(cids)))
    tasks = [asyncio.create_task(ingest_cid(c, con, sem, stats,
                                            args.timeout, args.retries))
             for c in cids]
    await asyncio.gather(*tasks)
    printer_task.cancel()
    try:
        await printer_task
    except asyncio.CancelledError:
        pass

    elapsed = time.time() - stats["start"]
    print(f"\n=== DONE in {elapsed/60:.1f} min ===")
    print(f"  cids done: {stats['done']:,}  failed: {stats['failed']:,}")
    print(f"  total NEW rows: {stats['new']:,}")
    print(f"  total API calls: {stats['calls']:,}")
    print(f"  avg new/cid: {stats['new']/max(stats['done'],1):.0f}")
    print(f"  avg calls/cid: {stats['calls']/max(stats['done'],1):.2f}")


if __name__ == "__main__":
    asyncio.run(main())
