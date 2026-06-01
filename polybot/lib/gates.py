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

BT_CROSS_BUCKET_NET_EV = 0.10
# Why 0.10: per-$1 PnL semantics (P2 fix 2026-05-26). Previously 0.07 was
# per-share PnL units (wr - mep), which under-counted true return by factor
# 1/ep ≈ 1.20× for ep=0.83 favorite (and 2× for ep=0.50). Re-cast in per-$1:
#   old 0.07 per-share @ ep=0.83 ≈ 0.084 per-$1.
# 0.10 leaves margin above this: bt nev > 0.10 → paper-implied ≈ 0.10 − drift
# (TBD, see BT_TO_PAPER_DRIFT below) ≥ 0.05 (PAPER_TO_LIVE_NET_EV) + variance buffer.
# TBD: recalibrate after first per-$1 re-mine cycle (count cross-bucket survivors).

MIN_N_HIT_PCT = 0.02
# Per-bucket: factor must hit ≥ 2% of events to qualify (stat significance floor)

MIN_N_HIT_ABS = 50
# but never below 50 absolute (handles small bucket V2 where 2% × 4000 = 80)


# ════════════════════════════════════════════════════════════════════════════
# Stage 2: bt → paper drift (measured empirically, paper paid more than bt expected)
# ════════════════════════════════════════════════════════════════════════════

BT_TO_PAPER_DRIFT = 0.015
# 1.5% measured 2026-05-25 on R-series paper trades — **entry-price space** drift:
# trade-based bt ep estimate vs paper book_ask ≈ +1.5 cents/share. This is the
# entry-price gap, independent of pnl formula (per-share or per-$1).
#
# CAVEAT (per-$1 nev semantics, post-P2 fix 2026-05-26):
# In per-$1 PnL space drift haircut is NOT constant — ep-dependent:
#   ep=0.83 favorite (R-series): ~1.8% per-trade  ← 0.015 approx OK
#   ep=0.50 even-money:          ~3.0% per-trade  ← 0.015 under-counts 2×
#   ep=0.16 underdog:            ~16%  per-trade  ← 0.015 under-counts 10×
# This constant is FAVORITE-CALIBRATED (R-series ep~0.83 era). For underdog
# (ep<0.4) factors, real paper drift is much larger — those factors MUST be
# probed for actual fillability + drift before paper deployment.
# TODO: replace with ep-dependent function in backtest_friction_ratio.


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
