"""Read-only CLI for paper_trade_5m_binary table.

Usage:
    uv run python cli.py status
    uv run python cli.py settled  [hypothesis]   # default: all
    uv run python cli.py open     [hypothesis]
    uv run python cli.py drift    [hypothesis]   # backtest vs paper EV
    uv run python cli.py rolling  [n]            # rolling per-trade backtest pnl
    uv run python cli.py log      [n]            # tail polybot.log
"""
import sys
import sqlite3
from config import DB_FILE

TABLE = "paper_trade_5m_binary"


def conn():
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    return c


def _hyp_filter(hyp):
    return ("AND hypothesis=? ", (hyp,)) if hyp else ("", ())


def cmd_status():
    c = conn()
    rows = c.execute(
        f"SELECT hypothesis, status, COUNT(*) n FROM {TABLE} GROUP BY 1,2 ORDER BY 1,2"
    ).fetchall()
    if not rows:
        print(f"{TABLE}: empty")
        return
    print(f"{'hyp':<6} {'status':<10} {'n':>5}")
    for r in rows:
        print(f"{r['hypothesis']:<6} {r['status']:<10} {r['n']:>5}")


def cmd_settled(hyp=None):
    c = conn()
    flt, args = _hyp_filter(hyp)
    rows = c.execute(
        f"SELECT id, hypothesis, candle_start_s, entry_price_backtest, entry_price_paper, "
        f"up_won, pnl_ratio_backtest, pnl_ratio_paper, pnl_ratio_drift "
        f"FROM {TABLE} WHERE status='settled' {flt}ORDER BY candle_start_s", args
    ).fetchall()
    bt_pnl = sum(r['pnl_ratio_backtest'] or 0 for r in rows)
    p_pnl  = sum(r['pnl_ratio_paper']    or 0 for r in rows if r['pnl_ratio_paper'] is not None)
    n_paper = sum(1 for r in rows if r['pnl_ratio_paper'] is not None)
    for r in rows:
        ep = r['entry_price_paper']
        ep_str = f"{ep:.3f}" if ep is not None else "n/a"
        pp = r['pnl_ratio_paper']
        pp_str = f"{pp:+.1%}" if pp is not None else "n/a"
        print(f"#{r['id']:>4} [{r['hypothesis']}] cs={r['candle_start_s']} "
              f"bt_entry={r['entry_price_backtest']:.3f} paper_entry={ep_str} "
              f"won={r['up_won']} bt={r['pnl_ratio_backtest']:+.1%} paper={pp_str}")
    n = len(rows)
    if n:
        print(f"\nN={n}  bt_avg={bt_pnl/n:+.2%}  paper_avg="
              f"{p_pnl/n_paper:+.2%}({n_paper} rows)" if n_paper else f"\nN={n} bt_avg={bt_pnl/n:+.2%} paper=n/a")


def cmd_open(hyp=None):
    c = conn()
    flt, args = _hyp_filter(hyp)
    rows = c.execute(
        f"SELECT id, hypothesis, candle_start_s, entry_price_backtest, entry_price_paper, opened_at_s "
        f"FROM {TABLE} WHERE status='open' {flt}ORDER BY id", args
    ).fetchall()
    for r in rows:
        ep = r['entry_price_paper']
        ep_str = f"{ep:.3f}" if ep is not None else "n/a"
        print(f"#{r['id']:>4} [{r['hypothesis']}] cs={r['candle_start_s']} "
              f"bt_entry={r['entry_price_backtest']:.3f} paper_entry={ep_str}")
    print(f"\n{len(rows)} open")


def cmd_drift(hyp=None):
    """Per-hypothesis: backtest avg pnl vs paper avg pnl, drift, n with paper price."""
    c = conn()
    flt, args = _hyp_filter(hyp)
    rows = c.execute(
        f"SELECT hypothesis, "
        f"COUNT(*) n_total, "
        f"AVG(pnl_ratio_backtest)               avg_bt, "
        f"AVG(pnl_ratio_paper)                  avg_paper, "
        f"AVG(pnl_ratio_drift)                  avg_drift, "
        f"SUM(CASE WHEN entry_price_paper IS NOT NULL THEN 1 ELSE 0 END) n_paper "
        f"FROM {TABLE} WHERE status='settled' {flt}GROUP BY hypothesis", args
    ).fetchall()
    if not rows:
        print("no settled trades")
        return
    print(f"{'hyp':<6} {'n':>4} {'n_paper':>8} {'avg_bt':>9} {'avg_paper':>10} {'drift':>9}")
    for r in rows:
        avg_bt = r['avg_bt'] or 0
        avg_p  = r['avg_paper']
        avg_d  = r['avg_drift']
        avg_p_s = f"{avg_p:+.2%}" if avg_p is not None else "n/a"
        avg_d_s = f"{avg_d:+.2%}" if avg_d is not None else "n/a"
        print(f"{r['hypothesis']:<6} {r['n_total']:>4} {r['n_paper']:>8} "
              f"{avg_bt:+.2%} {avg_p_s:>10} {avg_d_s:>9}")


def cmd_rolling(n=50):
    c = conn()
    rows = c.execute(
        f"SELECT id, hypothesis, pnl_ratio_backtest, pnl_ratio_paper FROM {TABLE} "
        f"WHERE status='settled' AND pnl_ratio_backtest IS NOT NULL "
        f"ORDER BY settled_at_s DESC LIMIT ?", (n,)
    ).fetchall()
    rows = list(reversed(rows))
    cum_bt = cum_p = wins = 0.0
    n_p = 0
    for i, r in enumerate(rows, 1):
        cum_bt += r['pnl_ratio_backtest']
        if r['pnl_ratio_backtest'] > 0:
            wins += 1
        if r['pnl_ratio_paper'] is not None:
            cum_p += r['pnl_ratio_paper']; n_p += 1
        pp = r['pnl_ratio_paper']
        pp_s = f"{pp:+6.1%}" if pp is not None else "  n/a "
        print(f"#{r['id']:>4} [{r['hypothesis']}] bt={r['pnl_ratio_backtest']:+6.1%} "
              f"paper={pp_s} avg_bt={cum_bt/i:+6.2%}")
    if rows:
        print(f"\nN={len(rows)} WR_bt={wins/len(rows):.0%} avg_bt={cum_bt/len(rows):+.2%}"
              f"{f'  avg_paper={cum_p/n_p:+.2%} (n={n_p})' if n_p else ''}")


def cmd_log(n=30):
    import subprocess
    subprocess.run(["tail", f"-{n}", "polybot.log"])


CMDS = {"status": cmd_status, "settled": cmd_settled, "open": cmd_open,
        "drift": cmd_drift, "rolling": cmd_rolling, "log": cmd_log}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = CMDS.get(cmd)
    if not fn:
        print(f"Usage: cli.py [{ '|'.join(CMDS) }] [arg]")
        sys.exit(1)
    args = sys.argv[2:]
    if cmd in ("rolling", "log") and args:
        fn(int(args[0]))
    elif cmd in ("settled", "open", "drift"):
        fn(args[0] if args else None)
    else:
        fn()
