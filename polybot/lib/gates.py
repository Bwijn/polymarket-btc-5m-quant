"""Single source of truth for all decision thresholds in the bt → paper → live pipeline.

Anywhere else referencing gate numbers (mining scripts, audit code, CLAUDE.md docs)
MUST import from here — DO NOT hardcode duplicates. Git tracks changes for audit.

Pipeline:
    bt mining            → bt cross-bucket gate    → cross-bucket survivor list
    bt survivor          + drift haircut           → paper-implied nev
    paper deploy         → paper graduation gate   → live deploy
    live in production   → live decay gate         → demote / kill

All values are measured empirically or set per Constitution rationale (CLAUDE.md).
Update with git commit + audit message when revising.
"""
from __future__ import annotations


# ════════════════════════════════════════════════════════════════════════════
# Stage 1: bt mining cross-bucket gate (factor survives if BOTH V1 and V2 pass)
# ════════════════════════════════════════════════════════════════════════════

BT_CROSS_BUCKET_NET_EV = 0.07
# Why 0.07: 1-pred mining on 1500 features × 99 thresholds × 2 sides ≈ 300k tests.
# Cross-bucket V1 ∩ V2 filter reduces winner's curse vs single-bucket. 0.07 leaves
# safety margin: paper-implied ≈ 0.07 − drift(0.015) = 0.055 (just above Constitution
# paper→live gate +5%). 2026-05-24 set after friction-fix re-mining produced 19
# cross-bucket candidates at this threshold.

MIN_N_HIT_PCT = 0.02
# Per-bucket: factor must hit ≥ 2% of events to qualify (stat significance floor)

MIN_N_HIT_ABS = 50
# but never below 50 absolute (handles small bucket V2 where 2% × 4000 = 80)


# ════════════════════════════════════════════════════════════════════════════
# Stage 2: bt → paper drift (measured empirically, paper paid more than bt expected)
# ════════════════════════════════════════════════════════════════════════════

BT_TO_PAPER_DRIFT = 0.015
# Why 0.015 (conservative): measured 2026-05-25 across 3 factors:
#   R2 UP   @ cs+90  n=85  drift=+1.57%  CI95 [+0.18%, +2.96%]
#   R4 DOWN @ cs+0   n=52  drift=-0.24%  CI95 [-0.97%, +0.49%]
#   H5 DOWN @ cs+60  n=156 drift=+0.90%  CI95 [+0.06%, +1.73%]
#   n-weighted average: +0.91%
# Conservative round up: 1.5% covers worst-direction observed drift with margin.
# Re-measure when new direction/et combinations enter paper.
# Applied as additive friction in backtest_friction_ratio (polybot/lib/friction.py).


# ════════════════════════════════════════════════════════════════════════════
# Stage 3: paper → live gate (Constitution, CLAUDE.md)
# ════════════════════════════════════════════════════════════════════════════

PAPER_TO_LIVE_NET_EV = 0.05
# Magnitude hurdle: net EV (扣 fee) 点估计 ≥ +5%
# Rationale: clean PM 7% taker fee + leave variance/decay margin.
# Small-edge factors (< 5% net) structurally not viable at this capital + fee tier.

PAPER_TO_LIVE_T_STAT = 1.65
# Statistical confidence: t > 1.65 = 95% CI lower bound > 0 (one-sided)

PAPER_TO_LIVE_CAP_N = 800
# Hard sample cap: ≈ (1.65 × σ / hurdle)² for σ ~80% per trade, hurdle 5%
# If factor doesn't graduate by n=800 → KILL (edge too small/slow to matter)

PAPER_TO_LIVE_CAP_WEEKS = 10
# Hard wall-clock cap: 8-10 weeks → KILL regardless of n (life is short)


# ════════════════════════════════════════════════════════════════════════════
# Self-test
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Sanity: implied chain
    bt = BT_CROSS_BUCKET_NET_EV
    paper_implied = bt - BT_TO_PAPER_DRIFT
    live_gate = PAPER_TO_LIVE_NET_EV
    assert paper_implied >= live_gate - 0.001, \
        f"chain broken: bt {bt} − drift {BT_TO_PAPER_DRIFT} = {paper_implied} < live gate {live_gate}"

    print("gates: OK")
    print(f"  bt mining gate (cross-bucket V1 ∩ V2): nev > {bt:.1%}")
    print(f"  bt → paper drift haircut             : −{BT_TO_PAPER_DRIFT:.1%}")
    print(f"  → paper-implied minimum               : {paper_implied:.1%}")
    print(f"  paper → live gate                     : nev > {live_gate:.1%} AND t > {PAPER_TO_LIVE_T_STAT}")
    print(f"  paper KILL cap                        : n > {PAPER_TO_LIVE_CAP_N} OR weeks > {PAPER_TO_LIVE_CAP_WEEKS}")
