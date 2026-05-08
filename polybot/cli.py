"""Read-only CLI for paper_trade_5m_binary table.

Usage:
    uv run python cli.py status
    uv run python cli.py settled  [hypothesis]   # default: all
    uv run python cli.py open     [hypothesis]
    uv run python cli.py rolling  [n]            # rolling per-trade ret over last n
    uv run python cli.py log      [n]            # tail polybot.log

Old copy-trade tables (paper_trades / real_trades) remain in the same db; use
`git checkout copy-archive-2026-05-08 -- polybot/cli.py` to query those.
"""
import sys
import sqlite3
from config import DB_FILE

TABLE = "paper_trade_5m_binary"


def conn():
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    return c


def _hyp_filter(hyp: str | None) -> tuple[str, tuple]:
    return ("AND hypothesis=? ", (hyp,)) if hyp else ("", ())


def cmd_status():
    c = conn()
    rows = c.execute(f"SELECT hypothesis, status, COUNT(*) c FROM {TABLE} GROUP BY 1,2 ORDER BY 1,2").fetchall()
    if not rows:
        print(f"{TABLE}: empty")
        return
    print(f"{'hyp':<6} {'status':<10} {'n':>5}")
    for r in rows:
        print(f"{r['hypothesis']:<6} {r['status']:<10} {r['c']:>5}")


def cmd_settled(hyp: str | None = None):
    c = conn()
    flt, args = _hyp_filter(hyp)
    rows = c.execute(
        f"SELECT id, hypothesis, candle_start, entry_price, up_won, pnl_pct, pnl_usd "
        f"FROM {TABLE} WHERE status='settled' {flt}ORDER BY candle_start", args
    ).fetchall()
    wins = sum(1 for r in rows if r['pnl_pct'] is not None and r['pnl_pct'] > 0)
    losses = sum(1 for r in rows if r['pnl_pct'] is not None and r['pnl_pct'] < 0)
    pnl = sum(r['pnl_usd'] or 0 for r in rows)
    for r in rows:
        tag = "WIN" if (r['pnl_pct'] or 0) > 0 else "LOSS"
        print(f"#{r['id']:>4} [{r['hypothesis']}] cs={r['candle_start']} "
              f"entry={r['entry_price']:.3f} up_won={r['up_won']} {tag} "
              f"pnl={r['pnl_pct']:+.1%} (${r['pnl_usd']:+.2f})")
    n = wins + losses
    if n:
        avg_pct = sum(r['pnl_pct'] for r in rows if r['pnl_pct'] is not None) / n
        print(f"\nN={n} W={wins} L={losses} WR={wins/n:.0%} "
              f"net=${pnl:+.2f} avg_pct={avg_pct:+.1%}")


def cmd_open(hyp: str | None = None):
    c = conn()
    flt, args = _hyp_filter(hyp)
    rows = c.execute(
        f"SELECT id, hypothesis, candle_start, entry_price, opened_at "
        f"FROM {TABLE} WHERE status='open' {flt}ORDER BY id", args
    ).fetchall()
    for r in rows:
        print(f"#{r['id']:>4} [{r['hypothesis']}] cs={r['candle_start']} "
              f"entry={r['entry_price']:.3f} opened_at={r['opened_at']}")
    print(f"\n{len(rows)} open")


def cmd_rolling(n: int = 50):
    c = conn()
    rows = c.execute(
        f"SELECT id, hypothesis, pnl_pct FROM {TABLE} "
        f"WHERE status='settled' AND pnl_pct IS NOT NULL "
        f"ORDER BY settled_at DESC LIMIT ?", (n,)
    ).fetchall()
    rows = list(reversed(rows))
    cum = wins = 0.0
    for i, r in enumerate(rows, 1):
        cum += r['pnl_pct']
        if r['pnl_pct'] > 0:
            wins += 1
        print(f"#{r['id']:>4} [{r['hypothesis']}] ret={r['pnl_pct']:+6.1%} "
              f"avg={cum/i:+6.2%}")
    if rows:
        print(f"\nN={len(rows)} WR={wins/len(rows):.0%} avg={cum/len(rows):+.2%}")


def cmd_log(n: int = 30):
    import subprocess
    subprocess.run(["tail", f"-{n}", "polybot.log"])


CMDS = {"status": cmd_status, "settled": cmd_settled, "open": cmd_open,
        "rolling": cmd_rolling, "log": cmd_log}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = CMDS.get(cmd)
    if not fn:
        print(f"Usage: cli.py [{ '|'.join(CMDS) }] [arg]")
        sys.exit(1)
    args = sys.argv[2:]
    if cmd in ("rolling", "log") and args:
        fn(int(args[0]))
    elif cmd in ("settled", "open"):
        fn(args[0] if args else None)
    else:
        fn()
