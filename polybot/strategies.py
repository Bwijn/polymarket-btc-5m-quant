"""Active paper-trade strategies — the runtime SSOT for scanner's ACTIVE tuple.

Lock-in spec: changes after lock = goal-post shift, enforced by git-tracking.
Killed strategies are NOT kept here — their audit lives in the factors table
(pm_btc5m.db, status='killed') + git history.
"""
from __future__ import annotations
from dataclasses import dataclass
from polybot.lib.expr_eval_v1 import validate


@dataclass(frozen=True)
class Strategy:
    label: str            # cosmetic log handle (e.g. 'min_intra_120_dn'); NEVER persisted —
    #                       expr is the sole identity (dedup key + db join key). See migration 001.
    expr: str             # trigger expression
    entry_offset_s: int   # seconds past candle_start to evaluate trigger + take entry price
    direction: str        # 'UP' | 'DOWN'
    live: bool = False    # place real orders (also gated by config.LIVE_ENABLED)
    slippage_cap: float = 0.03  # live BUY ceiling = entry_ep + cap. Co-located with the
    #                             factor (not a label-keyed config dict): the cap is part of
    #                             the factor's execution identity, same category as `live`.
    #                             EV-safe value ≈ breakeven_ep − typical_entry_ep. Default
    #                             0.03 = conservative inherit for un-tuned factors.

    def __post_init__(self):
        validate(self.expr)
        if self.direction not in ('UP', 'DOWN'):
            raise ValueError(f"bad direction {self.direction!r}")
        if not 0 <= self.slippage_cap < 0.5:    # money-path fat-finger guard
            raise ValueError(f"slippage_cap {self.slippage_cap} out of [0,0.5)")


# bt audit lives in the factors table (pm_btc5m.db) — query factor_panel for nev / n_hit / cycle_tag.
# Comments here only carry live-status / KILL rationale (info not in db).

# R4 — sole survivor of per-$1 re-eval (2026-06-01). even-money ep≈0.51.
# per-$1 bt V2(OOS) +19.9% ↔ paper +21.2% (n=126, t=2.52) — bt 预测 paper, drift-fix
# 验证成立. 过 paper→live gate (t>1.65 ∧ nev≥5%). GRADUATED 2026-06-02: live=True,
# 5% bankroll (paper n=131 t=2.45 nev+20.7%; 95% CI 下界+4.1% 擦 5% → 小额上线收窄 CI).
# 同 cycle 5 个 (R2/P1-P4) per-$1 OOS+paper 双弱 → killed (factors status, 2026-06-01).
# 'R4' = grandfathered label (历史 paper 行连续); 未来 factor 按 expr 索引, label 仅 log.
# slippage_cap=0.10 (was global 0.03 — too tight, FOK-killed the 9 highest-EV fills:
# paper +27% nev vs +1.3% on filled rows; real small-order slippage ≈0 so the cap, not
# book depth, was binding, verified 2026-06-21). 0.10 favors catching latency-fills over
# EV-margin: at avg entry 0.543 it allows price_limit 0.643 > breakeven_ep≈0.615 (wr 0.632)
# → rare fills in (0.615,0.643] are −EV, but bounded at −stake (no blowup) and entries
# seldom reach there. Strict-EV alternative = floor price_limit at breakeven_ep (deferred).
R4 = Strategy(
    'R4',
    'bn_taker_buy_ratio_pre_300>0.7554713487625122 & bn_vol_zscore_pre_60__zs24h>0.3679429590702057',
    entry_offset_s=0,
    direction='DOWN',
    live=True,
    slippage_cap=0.10,
)

# ── cohort lens_jaccard_20260614 — 11 dedup-rep, klines-only deploy (2026-06-15) ──
# 仅 3 个 bn_ klines 进 paper (零 4k-cap 污染). 6 个 trades-based defer: PM /trades 4k-cap
# 同时污染触发 candle 与 zs/rank 参考分布 → capped_frac 是不完整 filter; 干净 trades 史
# 只能前向 WS 录, 故 trades-dark 至下个 cycle. 2 个 futures rep runtime_ok=0. 全 entry=cs+30s.
# defer 决策 audit 在 factors (status=excluded); bt audit 同表 (factor_panel).
BN_CHG1800_ZS_UP = Strategy(
    'bn_chg1800_zs_up',
    'bn_chg_pct_pre_1800__zs7d<-2.831822395324707',
    entry_offset_s=30,
    direction='UP',
)
BN_TBR300_DN = Strategy(
    'bn_tbr300_dn',
    'bn_taker_buy_ratio_pre_300>0.8700732588768005',
    entry_offset_s=30,
    direction='DOWN',
)
BN_CHG3600_RANK_UP = Strategy(
    'bn_chg3600_rank_up',
    'bn_chg_pct_pre_3600__rank24h<0.0069444444961845875',
    entry_offset_s=30,
    direction='UP',
)

# ── cohort remine_20260618 — post trades→EP-purge re-mine on clean data ──
# Only #4 armed→paper: bn_ klines, runtime-computable. 独立于 R4 (return-corr 0.15,
# trigger-Jaccard 0.08) = 同向 diversifying 增量, 非冗余. #6 oi_chg_pct_1h survivor →
# factors status=excluded runtime_ok=0 (无 futures runtime path, 待补 infra); #3
# bn_chg3600_zs24h skip (与 BN_CHG3600_RANK_UP return-corr 0.64 冗余). bt audit → factor_panel.
BN_TBR900_RANK_DN = Strategy(
    'bn_tbr900_rank_dn',
    'bn_taker_buy_ratio_pre_900__rank24h>0.9930555820465088',
    entry_offset_s=30,
    direction='DOWN',
)

# ── cohort cvd_20260629 — signed-CVD (2·taker_buy−vol) taker-flow reversals ──
# 2 indep signals from 15-col CVD scorecard → dedup (busy-inclusive nb_fold + full
# return-corr matrix vs roster). A=60s reversal UP (全 roster 正交, max corr 0.065);
# B=5min reversal DOWN (唯一同向 +0.238 vs R4, 折叠线下=diversifying; FCFS et0 下 R4
# 抢锁 candle → 物理 overbet≈0). entry_offset_s=0: signal 纯后向→cs+0 已知, fixed-sample
# ep 路径证 et0 更便宜 (白嫖, 同 R4). zs7d 双生子 excluded (与 A redundant corr 0.82).
# bt audit → factors (factor_panel). armed→paper 起 OOS clock.
BN_CVD60_UP = Strategy(
    'cvd60_up',
    'bn_cvd_pre_60<-31.5946056',
    entry_offset_s=0,
    direction='UP',
)
BN_CVD300_DN = Strategy(
    'cvd300_dn',
    'bn_cvd_pre_300>74.55732519999998',
    entry_offset_s=0,
    direction='DOWN',
)

ACTIVE: tuple[Strategy, ...] = (
    R4,
    BN_CHG1800_ZS_UP, BN_TBR300_DN, BN_CHG3600_RANK_UP,
    BN_TBR900_RANK_DN,
    BN_CVD60_UP, BN_CVD300_DN,
)


if __name__ == '__main__':
    for s in ACTIVE:
        print(f"  {s.label}: {s.expr!r} entry=cs+{s.entry_offset_s}s bet={s.direction}")
    print("strategies: OK")
