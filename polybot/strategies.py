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


# R-series: paper_pick7_20260514 cherry-pick. Strategy.id = 'R<rank>',
# rank = paper_candidates.rank in pm_btc5m.db.

# R2: PM intra + slope_pre__rank24h. bt_nev=+8.45%, trig=3.9%, wr=81.0%, mean_ep=0.70
# 2026-05-24 — DEMOTED to paper-only. drift-fixed bt 重 mine 显示真 bt nev ≈ −0.8% (V1) / −0.2% (V2),
# friction (~3.9% @ mep 0.66) 吃光 gross_ev +3%. Paper +10.78% @ n=76 是 sample 噪声 (last 30 已 decay
# 到 +0.38%). 继续 paper 观察至 cap (~800 单) 或 8 周, 自然到 KILL.
R2 = Strategy(
    'R2',
    'min_intra_90_dn<0.3700000047683716 & slope_pre_60_dn__rank24h<0.1111111119389534',
    entry_offset_s=90,
    direction='UP',
    live=False,
)

# R4: bn_taker + bn_vol_zscore__zs24h. bt_nev=+7.53%, trig=4.0%, wr=63.1%, mean_ep=0.51 (drift-safe)
R4 = Strategy(
    'R4',
    'bn_taker_buy_ratio_pre_300>0.7554713487625122 & bn_vol_zscore_pre_60__zs24h>0.3679429590702057',
    entry_offset_s=0,
    direction='DOWN',
)

# P-series: 2pred_rep_20260526 cycle. 4 真独立信号 — Phase B mining 12 cross-bucket
# common dedup correlation cluster (corr<0.7 greedy) 后真 independent representative.
# 全 cs+60 entry, ep ≈ 0.83 (favorite side, fee 1.2%), 顺势 confirm BTC momentum 非 contrarian.
# correlation: P1↔P2 / P3↔P4 同 direction sub-mechanism corr 0.55-0.59; cross direction ~0.

# P1: delta_intra_60_dn + max_intra_30_dn__zs7d. v2_nev=+9.71%, n=139, ep=0.83
P1 = Strategy(
    'P1',
    'delta_intra_60_dn>0.18000000715255737 & max_intra_30_dn__zs7d>1.666505217552185',
    entry_offset_s=60,
    direction='DOWN',
)

# P2: delta_intra_60_up + delta_intra_30_up (双 delta 极负). v2_nev=+8.88%, n=142, ep=0.81
P2 = Strategy(
    'P2',
    'delta_intra_60_up<-0.2800000011920929 & delta_intra_30_up<-0.10999999940395355',
    entry_offset_s=60,
    direction='DOWN',
)

# P3: max_intra_30_up + delta_intra_60_dn__zs7d. v2_nev=+8.84%, n=141, ep=0.84
P3 = Strategy(
    'P3',
    'max_intra_30_up>0.7400000095367432 & delta_intra_60_dn__zs7d<-0.8927792906761169',
    entry_offset_s=60,
    direction='UP',
)

# P4: delta_intra_60_dn__zs7d + mean_intra_30_up. v2_nev=+7.25%, n=142, ep=0.85
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
