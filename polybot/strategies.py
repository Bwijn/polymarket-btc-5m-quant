"""Active paper-trade strategies. Lock-in spec — changes after lock = goal-post
shift, must be git-tracked (this file enforces it).

Mirrored from scratch/H5_*/strategy.py and scratch/H6_*/strategy.py
(historical decision contracts; this file is the runtime SSOT).
"""
from __future__ import annotations
from dataclasses import dataclass
from polybot.lib.expr_eval_v1 import validate


@dataclass(frozen=True)
class Strategy:
    id: str               # 'H5', 'H6', ...
    expr: str             # trigger expression
    entry_offset_s: int   # seconds past candle_start to evaluate trigger + take entry price
    direction: str        # 'UP' | 'DOWN'
    live: bool = False    # place real orders (also gated by config.LIVE_ENABLED)

    def __post_init__(self):
        validate(self.expr)
        if self.direction not in ('UP', 'DOWN'):
            raise ValueError(f"bad direction {self.direction!r}")


# H5: weekend dump cluster A — locked 2026-05-07
# 2026-05-16 rename: p_intra_60 → p_intra_60_up (direction-explicit schema, 触发条件不变)
H5 = Strategy('H5', 'p_intra_60_up<0.445 & is_weekend==1', entry_offset_s=60, direction='DOWN', live=True)

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
    live=True,
)

# R4: bn_taker + bn_vol_zscore__zs24h. bt_nev=+7.53%, trig=4.0%, wr=63.1%, mean_ep=0.51 (drift-safe)
R4 = Strategy(
    'R4',
    'bn_taker_buy_ratio_pre_300>0.7554713487625122 & bn_vol_zscore_pre_60__zs24h>0.3679429590702057',
    entry_offset_s=0,
    direction='DOWN',
)

# R7: bn_taker_60 + bn_vol_zscore__rank24h — killed 2026-05-21 (factor_decisions id=5)
# Paper n=33, net_ev -2.72%, gross_ev +0.40% (≈0). 死因: 零 alpha; bt_nev +6.54%
# 是 carry-forward + 选择性虚高假象. drift_t -2.33. Kept as artifact, not ACTIVE.
R7 = Strategy(
    'R7',
    'bn_taker_buy_ratio_pre_60>0.8810144662857056 & bn_vol_zscore_pre_60__rank24h>0.8125',
    entry_offset_s=0,
    direction='DOWN',
)

# R3: min_intra_up + bn_rv_1800__rank24h. bt_nev=+6.56%, trig=4.7%, wr=92.2%, mean_ep=0.83 (drift 风险)
R3 = Strategy(
    'R3',
    'min_intra_90_up<0.22499999403953552 & bn_rv_1800__rank24h>0.2291666716337204',
    entry_offset_s=90,
    direction='DOWN',
)

# R6: basis_pre__zs7d + bn_vol_zscore__zs7d — killed 2026-05-21 (factor_decisions id=6)
# Paper n=24, net_ev -11.4%, gross_ev -8.3%, winrate 50% (coin flip). 死因: 零 alpha
# (全 7 candidate net EV 最差). bt_nev +6.79% 是 carry-forward 假象. Kept as artifact.
R6 = Strategy(
    'R6',
    'basis_pre_60_up__zs7d<-1.564877986907959 & bn_vol_zscore_pre_60__zs7d>0.4064948856830597',
    entry_offset_s=0,
    direction='DOWN',
)

# R1, R3, R5 killed 2026-05-20 (factor_decisions id 2/3/4): classifier overfit /
# EV below floor. R7/R6 killed 2026-05-21 (id 5/6): 零 alpha (gross EV ≈ 0).
# Definitions kept above for audit; dropped from ACTIVE.
ACTIVE: tuple[Strategy, ...] = (H5, R2, R4)


if __name__ == '__main__':
    for s in ACTIVE:
        print(f"  {s.id}: {s.expr!r} entry=cs+{s.entry_offset_s}s bet={s.direction}")
    print("strategies: OK")
