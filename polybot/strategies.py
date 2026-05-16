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


# ─── paper_pick7_20260514 cherry-pick (V1 modeled / V2 OOS validated) ──────
# Deploy order = infra increment from smallest. Each Strategy.id = 'R<rank>' where
# rank refers to paper_candidates.rank in pm_btc5m.db (1-7 within this policy_tag).

# R5: PM intra + __rank24h. bt_nev=+4.99%, trig=5.7%, wr=90.8%, mean_ep=0.83 (drift 风险大)
R5 = Strategy(
    'R5',
    'delta_intra_90_dn<-0.27000001072883606 & delta_intra_60_dn__rank24h<0.5190972089767456',
    entry_offset_s=90,
    direction='UP',
)

# R1: PM intra + vol_ratio__zs24h. 触发率最高 (10.5%) 复利速度最快. bt_nev=+3.46%, wr=77.5%, mean_ep=0.71
R1 = Strategy(
    'R1',
    'delta_intra_75_dn<-0.0949999988079071 & vol_ratio_900_3600_dn__zs24h>0.37590160965919495',
    entry_offset_s=75,
    direction='UP',
)

# R2: PM intra + slope_pre__rank24h. bt_nev=+8.45%, trig=3.9%, wr=81.0%, mean_ep=0.70
R2 = Strategy(
    'R2',
    'min_intra_90_dn<0.3700000047683716 & slope_pre_60_dn__rank24h<0.1111111119389534',
    entry_offset_s=90,
    direction='UP',
)

ACTIVE: tuple[Strategy, ...] = (H5, R5, R1, R2)


if __name__ == '__main__':
    for s in ACTIVE:
        print(f"  {s.id}: {s.expr!r} entry=cs+{s.entry_offset_s}s bet={s.direction}")
    print("strategies: OK")
