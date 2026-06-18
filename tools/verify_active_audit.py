"""Dashboard SSOT gate: strategies.py ACTIVE expr == factors WHERE status∈(paper,live).

Enforces the code↔db mirror (双向): every strategy in polybot/strategies.py ACTIVE MUST
have a `factors` row with status 'paper'|'live', AND vice-versa (no orphan paper/live row).

Reason: `factors` 是单一 state-registry (merged paper_candidates+factor_decisions, 2026-06-15).
strategies.py = arming authority (constitution); factors.status = 其 mirror + bt audit 入口.
此 gate fail-fast 抓 drift: 加进 ACTIVE 但 status 没同步, 或 code 删了却留 paper/live 悬挂行.

Exit 0 = mirror in sync. Exit 1 = drift (两向都打印).
Recommended: deploy.sh hook (跟 verify_compute_ssot.py 并列).
"""
from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from polybot.strategies import ACTIVE

DB = '/home/polymarket_work/db/pm_btc5m.db'


def main():
    conn = sqlite3.connect(DB)
    db_active = {row[0] for row in conn.execute(
        "SELECT expr FROM factors WHERE status IN ('paper','live')")}
    conn.close()

    code_active = {s.expr for s in ACTIVE}
    only_code = code_active - db_active   # 在 ACTIVE 但 factors 无 paper/live 行
    only_db = db_active - code_active     # factors paper/live 但不在 ACTIVE

    if only_code or only_db:
        print('❌ ACTIVE ↔ factors(status paper/live) drift:')
        for e in sorted(only_code):
            print(f'   [only ACTIVE, db status≠paper/live] {e[:70]}')
        for e in sorted(only_db):
            print(f'   [only db paper/live, not in ACTIVE]  {e[:70]}')
        print()
        print('Fix: 同步 factors.status (UPDATE) 或 strategies.py ACTIVE 使两侧一致.')
        sys.exit(1)

    print(f'✓ ACTIVE ↔ factors mirror in sync ({len(code_active)} paper/live)')


if __name__ == '__main__':
    main()
