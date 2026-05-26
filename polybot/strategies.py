"""Active paper-trade strategies — the runtime SSOT for scanner's ACTIVE tuple.

Lock-in spec: changes after lock = goal-post shift, enforced by git-tracking.
Killed strategies are NOT kept here — their audit lives in factor_decisions
(pm_btc5m.db) + git history.
"""
from __future__ import annotations
from dataclasses import dataclass
from polybot.lib.expr_eval_v1 import validate


@dataclass(frozen=True)
class Strategy:
    id: str               # 'R2', 'R4', ...
    expr: str             # trigger expression
    entry_offset_s: int   # seconds past candle_start to evaluate trigger + take entry price
    direction: str        # 'UP' | 'DOWN'
    live: bool = False    # place real orders (also gated by config.LIVE_ENABLED)

    def __post_init__(self):
        validate(self.expr)
        if self.direction not in ('UP', 'DOWN'):
            raise ValueError(f"bad direction {self.direction!r}")


# bt audit lives in paper_candidates table — query for nev / n_hit / cycle_tag etc.
# Comments here only carry live-status / KILL rationale (info not in db).

# R2 — DEMOTED to paper-only 2026-05-24 (drift-fixed bt re-mine showed nev negative;
# paper sample 继续累至 cap/8 周自然 KILL)
R2 = Strategy(
    'R2',
    'min_intra_90_dn<0.3700000047683716 & slope_pre_60_dn__rank24h<0.1111111119389534',
    entry_offset_s=90,
    direction='UP',
    live=False,
)

R4 = Strategy(
    'R4',
    'bn_taker_buy_ratio_pre_300>0.7554713487625122 & bn_vol_zscore_pre_60__zs24h>0.3679429590702057',
    entry_offset_s=0,
    direction='DOWN',
)

# P-series — paper_candidates cycle_tag='2pred_rep_20260526'. bt audit in db.
P1 = Strategy(
    'P1',
    'delta_intra_60_dn>0.18000000715255737 & max_intra_30_dn__zs7d>1.666505217552185',
    entry_offset_s=60,
    direction='DOWN',
)

P2 = Strategy(
    'P2',
    'delta_intra_60_up<-0.2800000011920929 & delta_intra_30_up<-0.10999999940395355',
    entry_offset_s=60,
    direction='DOWN',
)

P3 = Strategy(
    'P3',
    'max_intra_30_up>0.7400000095367432 & delta_intra_60_dn__zs7d<-0.8927792906761169',
    entry_offset_s=60,
    direction='UP',
)

P4 = Strategy(
    'P4',
    'delta_intra_60_dn__zs7d<-0.48432302474975586 & mean_intra_30_up>0.608535885810852',
    entry_offset_s=60,
    direction='UP',
)

ACTIVE: tuple[Strategy, ...] = (R2, R4, P1, P2, P3, P4)


if __name__ == '__main__':
    for s in ACTIVE:
        print(f"  {s.id}: {s.expr!r} entry=cs+{s.entry_offset_s}s bet={s.direction}")
    print("strategies: OK")
