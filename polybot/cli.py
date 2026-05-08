import sys
import sqlite3
from config import DB_FILE

conn = sqlite3.connect(DB_FILE)
conn.row_factory = sqlite3.Row

cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

if cmd == "status":
    for table in ("real_trades", "paper_trades"):
        rows = conn.execute(f"SELECT status, COUNT(*) as c FROM {table} GROUP BY status").fetchall()
        counts = {r["status"]: r["c"] for r in rows}
        total = sum(counts.values())
        print(f"{table}: {counts} total={total}")

elif cmd == "settled":
    target = sys.argv[2] if len(sys.argv) > 2 else "real"
    table = f"{target}_trades"
    rows = conn.execute(f"SELECT id, title, entry_price, exit_price, pnl FROM {table} WHERE status='settled' ORDER BY id").fetchall()
    wins = losses = total_pnl = 0
    for r in rows:
        tag = "WIN" if r["pnl"] > 0 else "LOSE"
        if r["pnl"] > 0: wins += 1
        else: losses += 1
        total_pnl += r["pnl"]
        print(f"#{r['id']:>4} {tag}  pnl={r['pnl']:+.2f}  entry={r['entry_price']:.2f}  {r['title'][:50]}")
    n = wins + losses
    if n:
        print(f"\n{n} settled | W={wins} L={losses} | winrate={wins/n:.0%} | net PnL={total_pnl:+.2f} | avg EV={total_pnl/n:+.1%}")

elif cmd == "open":
    target = sys.argv[2] if len(sys.argv) > 2 else "real"
    table = f"{target}_trades"
    rows = conn.execute(f"SELECT id, title, entry_price, opened_at FROM {table} WHERE status='open' ORDER BY id").fetchall()
    for r in rows:
        print(f"#{r['id']:>4}  entry={r['entry_price']:.2f}  {r['title'][:55]}")
    print(f"\n{len(rows)} open")

elif cmd == "rolling":
    target = sys.argv[2] if len(sys.argv) > 2 else "paper"
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 50
    table = f"{target}_trades"
    rows = conn.execute(
        f"SELECT id, entry_price, size, pnl, source_wallet, title FROM {table} "
        f"WHERE status='settled' AND pnl IS NOT NULL ORDER BY closed_at DESC LIMIT ?", (n,)
    ).fetchall()
    rows = list(reversed(rows))
    cum = wins = 0
    for i, r in enumerate(rows, 1):
        ret = r["pnl"] / r["size"] * 100
        cum += ret
        if r["pnl"] > 0: wins += 1
        print(f"#{r['id']:>4}  ret={ret:+6.1f}%  roll={cum/i:+6.2f}%  entry={r['entry_price']:.2f}  {r['source_wallet'][:8]}..  {r['title'][:40]}")
    if rows:
        N = len(rows)
        pnl = sum(r["pnl"] for r in rows)
        print(f"\nN={N} | WR={wins/N:.0%} | total_pnl={pnl:+.2f} | mean_ret={cum/N:+.2f}%")

elif cmd == "wallets":
    target = sys.argv[2] if len(sys.argv) > 2 else "paper"
    window = int(sys.argv[3]) if len(sys.argv) > 3 else 30
    table = f"{target}_trades"
    rows = conn.execute(
        f"SELECT source_wallet, size, pnl FROM {table} "
        f"WHERE status='settled' AND pnl IS NOT NULL ORDER BY closed_at"
    ).fetchall()
    from collections import defaultdict
    per = defaultdict(list)
    for r in rows:
        per[r["source_wallet"]].append(r)
    stats = []
    for w, trades in per.items():
        n = len(trades)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        pnl = sum(t["pnl"] for t in trades)
        rets = [t["pnl"] / t["size"] * 100 for t in trades]
        mean = sum(rets) / n
        k = min(window, n)
        tail = trades[-k:]
        roll = sum(t["pnl"] / t["size"] * 100 for t in tail) / k
        roll_wr = sum(1 for t in tail if t["pnl"] > 0) / k
        stats.append((w, n, wins, pnl, mean, roll, roll_wr, k))
    stats.sort(key=lambda x: (x[5] - x[4]))  # delta asc: 最恶化的在最上
    print(f"wallet          N    WR    pnl         EV      roll{window}_WR  roll{window}_EV   delta")
    for w, n, wins, pnl, mean, roll, roll_wr, k in stats:
        tag = "" if k == window else f" (only {k})"
        delta = roll - mean
        print(f"{w[:12]}  N={n:>4}  {wins/n:>3.0%}  {pnl:>+8.1f}   {mean:>+7.2f}%    {roll_wr:>4.0%}    {roll:>+7.2f}%   {delta:>+7.2f}{tag}")
    print("\nprofiles (delta asc):")
    for w, *_ in stats:
        print(f"  {w[:12]}  https://polymarket.com/profile/{w}  |  https://polymarketanalytics.com/traders/{w}")

elif cmd == "trend":
    target = sys.argv[2] if len(sys.argv) > 2 else "paper"
    min_per_bucket = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    table = f"{target}_trades"
    rows = conn.execute(
        f"SELECT source_wallet, size, pnl FROM {table} "
        f"WHERE status='settled' AND pnl IS NOT NULL ORDER BY closed_at"
    ).fetchall()
    from collections import defaultdict
    per = defaultdict(list)
    for r in rows:
        per[r["source_wallet"]].append(r)
    stats = []
    skipped = []
    for w, trades in per.items():
        n = len(trades)
        # 自适应 buckets: 每桶保证 >= min_per_bucket, 最多 6 桶, 最少 3 桶
        buckets = min(6, max(3, n // min_per_bucket))
        if n < 3 * min_per_bucket:
            skipped.append((w, n))
            continue
        rets = [t["pnl"] / t["size"] * 100 for t in trades]
        bsize = n // buckets
        bucket_evs = []
        bucket_ns = []
        for i in range(buckets):
            start = i * bsize
            end = n if i == buckets - 1 else (i + 1) * bsize
            b = rets[start:end]
            bucket_evs.append(sum(b) / len(b))
            bucket_ns.append(len(b))
        mono = sum(1 if bucket_evs[i] > bucket_evs[i-1] else -1 for i in range(1, buckets))
        slope = bucket_evs[-1] - bucket_evs[0]
        stats.append((w, n, buckets, bucket_evs, bucket_ns, mono, slope))
    stats.sort(key=lambda x: (-x[6], -x[5]))
    print(f"(min per bucket: {min_per_bucket})")
    for w, n, bkts, evs, bns, mono, slope in stats:
        ev_str = "  ".join(f"{e:>+6.1f}%(n={bn})" for e, bn in zip(evs, bns))
        print(f"{w[:12]}  N={n:>4}  buckets={bkts}  slope={slope:>+6.1f}  mono={mono:>+2d}")
        print(f"              EVs: {ev_str}")
    if skipped:
        print(f"\nskipped ({len(skipped)}, need N >= {3 * min_per_bucket}): " +
              ", ".join(f"{w[:12]}(N={n})" for w, n in skipped))

elif cmd == "log":
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    import subprocess
    subprocess.run(["tail", f"-{n}", "polybot.log"])

else:
    print("Usage: cli.py [status|settled|open|rolling|wallets|trend|log] [real|paper] [n]")
