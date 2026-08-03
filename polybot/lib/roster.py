"""Factor roster — loads the ACTIVE tuple from the factor_roster table (data/algo split).

Context: roster was `polybot/strategies.py` (code) until 2026-08-03; it is now data.
  Code holds the *algorithm* (validation rules + runtime semantics), the db holds *which
  factors are armed*. Editing surface = db/roster.db (local); prod reads factor_roster in
  polybot.db, same db as paper_trade_5m_binary → dashboard joins need no ATTACH.
Source: factor_roster (expr PK; status IN paper|live is the arming switch).
Expected: load_roster() returns the same tuple[Strategy, ...] scanner consumed before, so
  every downstream usage (strat.expr/.direction/.live/.slippage_cap/.label) is unchanged.

Validation is the reason this is code, not a view: CHECK constraints cover direction and
the slippage_cap fat-finger range, but `validate(expr)` needs the Python parser — a typo'd
expr in a db row would otherwise reach the money path unchecked. Every row is parsed here
before it can arm.
"""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass

from polybot.lib.expr_eval_v1 import validate

ARMED = ('paper', 'live')


@dataclass(frozen=True)
class Strategy:
    label: str            # log handle + dashboard identity; expr stays the sole join key
    expr: str             # trigger expression
    entry_offset_s: int   # seconds past candle_start to evaluate trigger + take entry price
    direction: str        # 'UP' | 'DOWN'
    live: bool = False    # place real orders (also gated by config.LIVE_ENABLED)
    slippage_cap: float = 0.03   # live BUY ceiling = entry_ep + cap
    bankroll_frac: float | None = None   # per-trade size = wallet_usdc × frac; required iff live

    def __post_init__(self):
        validate(self.expr)
        if self.direction not in ('UP', 'DOWN'):
            raise ValueError(f"{self.label}: bad direction {self.direction!r}")
        if not 0 <= self.slippage_cap < 0.5:    # money-path fat-finger guard
            raise ValueError(f"{self.label}: slippage_cap {self.slippage_cap} out of [0,0.5)")
        if self.live and not self.bankroll_frac:
            raise ValueError(f"{self.label}: status=live but no bankroll_frac — refusing to arm")


def load_roster(db_path: str) -> tuple[Strategy, ...]:
    """Read armed factors from factor_roster. Raises on any invalid row — fail loud at
    startup beats a silently-skipped factor discovered a week later in the paper counts."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT label, expr, entry_offset_s, direction, status, slippage_cap, bankroll_frac "
            f"FROM factor_roster WHERE status IN {ARMED} ORDER BY label").fetchall()
    finally:
        con.close()
    out = []
    for label, expr, et, direction, status, slip, frac in rows:
        if not label:
            raise ValueError(f"armed factor has no label: {expr}")
        out.append(Strategy(label=label, expr=expr, entry_offset_s=et, direction=direction,
                            live=(status == 'live'), slippage_cap=slip, bankroll_frac=frac))
    return tuple(out)
