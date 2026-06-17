"""Sanity test: per-$1 PnL formula = paper SSOT.

Context: 验证 mining 即将切到的 per-$1 PnL formula
  pnl = my_won ? (1-ep)/ep : -1
跟 paper db `pnl_ratio_paper` 完全一致 — 这是 mining/paper formula 对齐的契约.

Source: db/polybot_live.db paper_trade_5m_binary settled rows
Expected: 100% byte-equal (0 mismatch within atol 1e-9)
"""
from __future__ import annotations
import sqlite3
import sys

DB = '/home/polymarket_work/db/polybot_live.db'
ATOL = 1e-9


def main():
    con = sqlite3.connect(DB)
    rows = con.execute("""
        SELECT id, direction, up_won, entry_price_paper, pnl_ratio_paper
        FROM paper_trade_5m_binary
        WHERE pnl_ratio_paper IS NOT NULL
          AND entry_price_paper IS NOT NULL
          AND up_won IS NOT NULL
    """).fetchall()
    con.close()

    n = len(rows)
    print(f'sampled {n} settled paper rows')

    mismatches = []
    for r in rows:
        rid, direction, up_won, ep, db_pnl = r
        my_won = (up_won == 1) if direction == 'UP' else (up_won == 0)
        expected_pnl = (1.0 - ep) / ep if my_won else -1.0
        diff = abs(expected_pnl - db_pnl)
        if diff > ATOL:
            mismatches.append((rid, direction, up_won, ep, db_pnl, expected_pnl, diff))

    if mismatches:
        print(f'❌ {len(mismatches)}/{n} mismatch:')
        for m in mismatches[:5]:
            print(f'   id={m[0]} dir={m[1]} won={m[2]} ep={m[3]:.4f} '
                  f'db={m[4]:.6f} expected={m[5]:.6f} diff={m[6]:.2e}')
        sys.exit(1)

    print(f'✓ ALL {n} rows: per-$1 PnL formula byte-equal paper db.')
    print(f'  formula = (1-ep)/ep if my_won else -1')
    print(f'  → mining 切到此 formula = paper SSOT 对齐.')


if __name__ == '__main__':
    main()
