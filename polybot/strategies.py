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

# R4 — sole survivor of per-$1 re-eval (2026-06-01). even-money ep≈0.51.
# per-$1 bt V2(OOS) +19.9% ↔ paper +21.2% (n=126, t=2.52) — bt 预测 paper, drift-fix
# 验证成立. 过 paper→live gate (t>1.65 ∧ nev≥5%). graduate 候选, 待 magnitude CI 收窄.
# 同 cycle 5 个 (R2/P1-P4) per-$1 OOS+paper 双弱 → killed (factor_decisions 2026-06-01).
# id='R4' grandfathered (历史 paper 行连续); 未来 factor 弃 abbreviation, 按 expr 索引.
R4 = Strategy(
    'R4',
    'bn_taker_buy_ratio_pre_300>0.7554713487625122 & bn_vol_zscore_pre_60__zs24h>0.3679429590702057',
    entry_offset_s=0,
    direction='DOWN',
)

ACTIVE: tuple[Strategy, ...] = (R4,)


if __name__ == '__main__':
    for s in ACTIVE:
        print(f"  {s.id}: {s.expr!r} entry=cs+{s.entry_offset_s}s bet={s.direction}")
    print("strategies: OK")
