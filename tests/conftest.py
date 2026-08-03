"""pytest bootstrap — put repo root on sys.path so `polybot.*` imports resolve.

Tests live at repo root (tests/, NOT polybot/) so they never deploy to VPS.
Run in the polybot venv (has runtime deps): uv run --project polybot python -m pytest tests/

Also holds ROSTER_SQL: the roster is data in prod polybot.db (edited there directly), so
the repo keeps no copy of it. Tests pin their own factor_roster instead — the
load→compute→evaluate path stays covered without coupling the suite to what is armed today.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Real exprs (so expr_eval_v1 actually parses them): 2-leg interaction + 1-leg, plus a
# killed row that must NOT load — pins load_roster's status filter.
ROSTER_SQL = """
CREATE TABLE factor_roster (
  expr TEXT PRIMARY KEY, label TEXT UNIQUE,
  fam TEXT, base TEXT, transform TEXT, tail TEXT, thresh REAL, n_legs INTEGER DEFAULT 1,
  direction TEXT NOT NULL CHECK(direction IN ('UP','DOWN')),
  entry_offset_s INTEGER DEFAULT 0,
  slippage_cap REAL DEFAULT 0.03 CHECK(slippage_cap >= 0 AND slippage_cap < 0.5),
  bankroll_frac REAL CHECK(bankroll_frac IS NULL OR (bankroll_frac > 0 AND bankroll_frac <= 0.5)),
  status TEXT NOT NULL CHECK(status IN ('candidate','paper','live','killed','excluded')),
  armed_at INTEGER);
INSERT INTO factor_roster (expr,label,direction,entry_offset_s,slippage_cap,status) VALUES
 ('bn_taker_buy_ratio_pre_300>0.7554713487625122 & bn_vol_zscore_pre_60__zs24h>0.3679429590702057',
  'fx_two_leg','DOWN',0,0.10,'paper'),
 ('bn_cvd_pre_60<-31.5946056','fx_one_leg','UP',0,0.03,'paper'),
 ('bn_cvd_pre_300>74.55732519999998','fx_killed','DOWN',0,0.03,'killed');
"""
