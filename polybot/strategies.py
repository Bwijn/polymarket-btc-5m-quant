"""Active paper-trade strategies. Lock-in spec — changes after lock = goal-post
shift, must be git-tracked (this file enforces it).

Mirrored from scratch/H5_*/strategy.py and scratch/H6_*/strategy.py
(historical decision contracts; this file is the runtime SSOT).
"""
from __future__ import annotations
from dataclasses import dataclass
from factor.expr_eval_v1 import validate


@dataclass(frozen=True)
class Strategy:
    id: str               # 'H5', 'H6', ...
    expr: str             # trigger expression
    entry_offset_s: int   # seconds past candle_start to evaluate trigger + take entry price
    direction: str        # 'UP' | 'DOWN'

    def __post_init__(self):
        validate(self.expr)
        if self.direction not in ('UP', 'DOWN'):
            raise ValueError(f"bad direction {self.direction!r}")


# H5: weekend dump cluster A — locked 2026-05-07
# 2026-05-16 rename: p_intra_60 → p_intra_60_up (direction-explicit schema, 触发条件不变)
H5 = Strategy('H5', 'p_intra_60_up<0.445 & is_weekend==1', entry_offset_s=60, direction='DOWN')

# H6: max-intra-120 extreme dump — killed 2026-05-11 (factor_decisions id=1)
# Paper n=43, net_ev -1.55%, entry drift +5.6¢ 吃光 alpha. Kept as artifact, not ACTIVE.
H6 = Strategy('H6', 'max_intra_120_up<0.4', entry_offset_s=120, direction='DOWN')

ACTIVE: tuple[Strategy, ...] = (H5,)


if __name__ == '__main__':
    for s in ACTIVE:
        print(f"  {s.id}: {s.expr!r} entry=cs+{s.entry_offset_s}s bet={s.direction}")
    print("strategies: OK")
