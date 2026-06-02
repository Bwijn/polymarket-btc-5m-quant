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


# bt audit lives in paper_candidates table — query for nev / n_hit / cycle_tag etc.
# Comments here only carry live-status / KILL rationale (info not in db).

# R4 — sole survivor of per-$1 re-eval (2026-06-01). even-money ep≈0.51.
# per-$1 bt V2(OOS) +19.9% ↔ paper +21.2% (n=126, t=2.52) — bt 预测 paper, drift-fix
# 验证成立. 过 paper→live gate (t>1.65 ∧ nev≥5%). GRADUATED 2026-06-02: live=True,
# 5% bankroll (paper n=131 t=2.45 nev+20.7%; 95% CI 下界+4.1% 擦 5% → 小额上线收窄 CI).
# 同 cycle 5 个 (R2/P1-P4) per-$1 OOS+paper 双弱 → killed (factor_decisions 2026-06-01).
# 'R4' = grandfathered label (历史 paper 行连续); 未来 factor 按 expr 索引, label 仅 log.
R4 = Strategy(
    'R4',
    'bn_taker_buy_ratio_pre_300>0.7554713487625122 & bn_vol_zscore_pre_60__zs24h>0.3679429590702057',
    entry_offset_s=0,
    direction='DOWN',
    live=True,
)

# ── cohort per_dollar_20260602 — 5 independent signals entering paper ──────────
# dedup'd from 199 cross-bucket survivors (overlap-coef cluster, gates.FACTOR_DEDUP*).
# 2 underdog + 3 mid · 3 UP + 2 DOWN. All INTRA (trades-based, no klines).
# Self-prove via paper EV; R4 = live bench. bt audit → paper_candidates table.
# id = mechanism shorthand (no R/P abbreviation; NEXT-3 will switch dedup to expr).
MIN120DN = Strategy(
    'min_intra_120_dn',
    'min_intra_120_dn__rank24h>0.971731424331665 & min_intra_120_dn__zs7d>1.6201647520065308',
    entry_offset_s=120,
    direction='UP',
)
MIN180UP = Strategy(
    'min_intra_180_up',
    'min_intra_180_up__zs7d>1.6483670473098755',
    entry_offset_s=180,
    direction='DOWN',
)
CHGRATE120UP = Strategy(
    'chg_rate_120_up',
    'chg_rate_intra_120_up<117.5 & chg_rate_intra_120_up__zs7d<-1.5178922414779663',
    entry_offset_s=120,
    direction='UP',
)
CHGRATE120DN = Strategy(
    'chg_rate_120_dn',
    'chg_rate_intra_120_dn__zs24h<-1.4102975130081177',
    entry_offset_s=120,
    direction='DOWN',
)
STD180UP = Strategy(
    'std_180_up',
    'std_intra_180_up<0.04648745432496071',
    entry_offset_s=180,
    direction='UP',
)

ACTIVE: tuple[Strategy, ...] = (
    R4, MIN120DN, MIN180UP, CHGRATE120UP, CHGRATE120DN, STD180UP,
)


if __name__ == '__main__':
    for s in ACTIVE:
        print(f"  {s.label}: {s.expr!r} entry=cs+{s.entry_offset_s}s bet={s.direction}")
    print("strategies: OK")
