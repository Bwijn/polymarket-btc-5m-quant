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

    def __post_init__(self):
        validate(self.expr)
        if self.direction not in ('UP', 'DOWN'):
            raise ValueError(f"bad direction {self.direction!r}")


# bt audit lives in the factors table (pm_btc5m.db) — query factor_panel for nev / n_hit / cycle_tag.
# Comments here only carry live-status / KILL rationale (info not in db).

# R4 — sole survivor of per-$1 re-eval (2026-06-01). even-money ep≈0.51.
# per-$1 bt V2(OOS) +19.9% ↔ paper +21.2% (n=126, t=2.52) — bt 预测 paper, drift-fix
# 验证成立. 过 paper→live gate (t>1.65 ∧ nev≥5%). GRADUATED 2026-06-02: live=True,
# 5% bankroll (paper n=131 t=2.45 nev+20.7%; 95% CI 下界+4.1% 擦 5% → 小额上线收窄 CI).
# 同 cycle 5 个 (R2/P1-P4) per-$1 OOS+paper 双弱 → killed (factors status, 2026-06-01).
# 'R4' = grandfathered label (历史 paper 行连续); 未来 factor 按 expr 索引, label 仅 log.
R4 = Strategy(
    'R4',
    'bn_taker_buy_ratio_pre_300>0.7554713487625122 & bn_vol_zscore_pre_60__zs24h>0.3679429590702057',
    entry_offset_s=0,
    direction='DOWN',
    live=True,
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

ACTIVE: tuple[Strategy, ...] = (
    R4,
    BN_CHG1800_ZS_UP, BN_TBR300_DN, BN_CHG3600_RANK_UP,
)


if __name__ == '__main__':
    for s in ACTIVE:
        print(f"  {s.label}: {s.expr!r} entry=cs+{s.entry_offset_s}s bet={s.direction}")
    print("strategies: OK")
