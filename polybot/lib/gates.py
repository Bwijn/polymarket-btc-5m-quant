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
# Why 0.10: per-$1 PnL semantics. Previously 0.07 was
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
# Stage 1.5: cross-bucket survivor dedup (mining variants → independent signals)
# ════════════════════════════════════════════════════════════════════════════

FACTOR_DEDUP_OVERLAP_MAX = 0.45
# Two same-direction factors are the SAME signal if hit-candle overlap-coef
# |A∩B|/min(|A|,|B|) ≥ this. Set at the empty valley of the bimodal pairwise-overlap
# distribution (cycle 2026-06-02 re-mine: independent mode ≤0.30, dup mode ≥0.85,
# valley 0.30-0.50 near-empty → robust, T∈[0.40,0.50] = identical partition).
# Greedy keeps one representative per independent cluster.

FACTOR_DEDUP_CAPEFF_TIEBREAK = 0.15
# Representative within a cluster = max capital-efficiency (freq×nev, freq = V2 fwd
# hits/wk). But capeff is often flat across a cluster while nev varies ~2× → pure
# capeff-argmax picks a fragile low-nev/high-freq extreme. Guard: among members
# within 15% of cluster max capeff, pick the HIGHEST nev (more margin above the 5%
# live hurdle, less decay/estimation risk). Per Constitution EV>MDD: when capeff is
# within noise, prefer the larger, safer edge.


# ════════════════════════════════════════════════════════════════════════════
# Stage 2: bt → paper drift (measured empirically, paper paid more than bt expected)
# ════════════════════════════════════════════════════════════════════════════

BT_TO_PAPER_DRIFT = 0.015
# Fixed +1.5 cents/share execution drift in ENTRY-PRICE space (measured 2026-05-25
# on R-series paper: trade-based bt ep vs paper book_ask). Applied in ep-space by
# friction.backtest_friction_ratio as fee(ep + drift); mine_gpu likewise uses
# (ep + drift) in the PnL term. Living in ep-space makes the per-$1 nev haircut
# naturally ep-dependent (larger for low ep via 1/ep) — underdog is haircut MORE,
# not less. Conservative by design → bt nev stays trustworthy across all ep buckets.


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
    assert 0 < FACTOR_DEDUP_OVERLAP_MAX < 1, "dedup overlap threshold must be in (0,1)"
    assert 0 < FACTOR_DEDUP_CAPEFF_TIEBREAK < 1, "dedup capeff tiebreak must be in (0,1)"

    print("gates: OK")
    print(f"  bt mining gate (cross-bucket V1 ∩ V2): nev > {bt:.1%}")
    print(f"  bt → paper drift haircut             : −{BT_TO_PAPER_DRIFT:.1%}")
    print(f"  → paper-implied minimum               : {paper_implied:.1%}")
    print(f"  paper → live gate                     : nev > {live_gate:.1%} AND t > {PAPER_TO_LIVE_T_STAT}")
    print(f"  paper KILL cap                        : n > {PAPER_TO_LIVE_CAP_N} OR weeks > {PAPER_TO_LIVE_CAP_WEEKS}")
