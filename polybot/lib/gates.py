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
# Per-$1 PnL semantics. Leaves margin above the paper hurdle: bt nev > 0.10 →
# paper-implied ≈ 0.10 − drift(0.015) = 0.085 ≥ 0.05 (PAPER_TO_LIVE_NET_EV) + variance buffer.

MIN_N_HIT_PCT = 0.02
# Per-bucket: factor must hit ≥ 2% of events to qualify (stat significance floor)

MIN_N_HIT_ABS = 50
# but never below 50 absolute (handles small bucket V2 where 2% × 4000 = 80)


# ════════════════════════════════════════════════════════════════════════════
# Stage 1b: 1-pred lens hurdles (NS1 refactor — compute/selection split)
# ════════════════════════════════════════════════════════════════════════════
# Selection moved from ONE baked-in nev gate to MULTIPLE lenses over the policy-free
# measurements table (scratch/research/mine). Keep (∪) = passes ANY edge lens; lens_count
# (how many) = trust signal (∩ core = all pass = most robust). Each lens is a UNIFORM rule
# (NOT per-factor tuned — per-factor threshold pick = in-sample overfit / p-hacking).
# nev lens reuses BT_CROSS_BUCKET_NET_EV above.

NET_BASE_MIN = 0.08
# net-base lens: net_base = wr/ep − 1 − friction(ep) (tail-AGNOSTIC floor, no 1/ep convexity)
# must be > this. Catches thin/convex factors nev lets through — the even-money BAND can't:
# band filters mean(ep), but convexity mirage comes from per-trade ep VARIANCE (low-ep wins
# inflate nev; net_base, on aggregate wr/ep, stays flat). 0.08 (was 0.0 = noise floor): on the
# 1-pred run, 0→0.08 cut ∪keep 32k→8.8k (net_base lens was admitting 31k on >0 alone) + mirage
# in fit-for-size 25→2 (chg_rate/std/rng_intra), while the 3-lens fit only fell 218→174 — the
# 44 dropped were all <8% base (bn_ cross-venue + delta/asym/pmt real survivors untouched;
# median net_base 0.160→0.168).

WF_MIN_WEEK_N = 10
# walk-forward lens: a weekly window with < this many hits has too-noisy nev to read its
# SIGN → not counted toward the sign-consistency fraction.

WF_MIN_WEEKS = 4
# walk-forward lens: need ≥ this many COUNTABLE weeks (n ≥ WF_MIN_WEEK_N) to assess
# consistency; fewer ⇒ temporal coverage too thin to judge → lens fails.

WF_SIGN_FRAC = 0.65
# walk-forward lens: fraction of countable weeks with nev_d > 0 must be ≥ this. A persistent
# ~10% edge averages ~73% positive weeks (per-week SE ~16%); a regime-concentrated edge ~50%.
# 0.65 separates "persistent" from "good-regime-only" while tolerating per-week noise.
# Sign (not magnitude) because per-week n is small — sign is the robust statistic.

EP_BAND_LO = 0.35
EP_BAND_HI = 0.65
# Fit-for-size band: small SINGLE-factor capital can't diversify variance, so BOTH ep extremes
# are ruin-risk and are excluded from primary selection (kept/tagged, deferred until 10+
# independent factors can diversify variance — Constitution tail clause):
#   - longshot (ep < LO): nev/net_base inflated by 1/ep but high per-bet variance + unreliable
#     low-ep winrate + thin-book executability → one drawdown can ruin.
#   - favorite (ep > HI): tiny per-bet return (1/ep−1 small) + 7% fee eats the edge + wr→1
#     small-sample overconfidence → capital-inefficient, slow compounding.
# Even-money is the sweet spot: liquid, survivable variance, reliable, fee manageable. R4
# (ep≈0.51) lives here. NOTE: nev AND net_base both carry δ/ep leverage → both rank longshots
# top; the band (not the rank metric) is what excludes them. Within-band rank by net_base
# (convexity-stripped). Principled alt = risk-adjusted t-stat (ep cancels): winrate shrinkage
# (wr→1 degeneracy) = WILSON_Z below (DONE); risk-adjusted t-stat itself (Σpayoff²) → effN, deferred.

WILSON_Z = 1.0
# Winrate shrinkage (the EP_BAND-comment's deferred "principled alt"): bt net_base uses the
# Wilson score LOWER bound of wr at this z in place of the point wr → net_base becomes n-AWARE
# (small-n / wr→1 estimates shrunk toward conservative; → point wr as n→∞), penalizing the
# selection-bias flukes the magnitude gate alone lets through. z=1.0 (≈84% one-sided = 1 SE):
# bt is a PRE-SCREEN, not proof (paper-t is the proof) → 1 SE demotes small-n flukes without
# draconian over-kill of genuine-but-sparse edge. WHY in-sample shrinkage not in-sample t-test:
# FWE (253k candidates) makes any in-sample significance a lie; Wilson only down-RANKS suspects
# before the paper trial, it does not certify. Tunable.


# ════════════════════════════════════════════════════════════════════════════
# Stage 1.5: cross-bucket survivor dedup (mining variants → independent signals)
# ════════════════════════════════════════════════════════════════════════════

FACTOR_DEDUP_JACCARD_MAX = 0.75
# Two same-direction factors = SAME hypothesis (FWE hygiene) if their tradeable-candle
# trigger sets have Jaccard |A∩B|/|A∪B| > this. Greedy leader-selection: sort
# net_base_wilson desc, fold a factor into an already-kept rep iff same-dir AND Jaccard
# > this; else it seeds a new rep. Trigger set = feature condition ∩ entry_valid.
# WHY Jaccard not containment |A∩B|/min (retired 2026-06-13, was 0.45): containment is
# asymmetric (one-way "B⊂A special case") → over-folds a rare narrow factor into any
# broad one whose candles cover it, losing a distinct sub-hypothesis forever; Jaccard is
# symmetric (two-way A⟺B = same hypothesis). 2026-06 re-mine: the two barely diverge
# (same-dir pair size-ratio median 1.0, no extreme nesting) → robustness/principle choice,
# error direction 宽进-safe (mis-fold loses a hypothesis irreversibly; mis-keep = mild
# waste, paper-t kills). No valley (smooth gradient: Jaccard 0.65→40 / 0.75→52 / 0.85→64).
# Also: intersection-corr dedup (交集口径, 只共同触发 candle) retired — degenerate
# (same-dir share `won` → corr≈±1, no variance). Its non-degenerate successor =
# full-stream corr (全流口径, 含未触发 0) lives as a REFERENCE lens (not a hard gate):
# metrics.py:stream_corr (math SSOT) + drop-k mirage 稳健化, applied per-cohort dedup.
# Jaccard remains the sole hard fold gate here.


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
    assert 0 < FACTOR_DEDUP_JACCARD_MAX < 1, "dedup Jaccard threshold must be in (0,1)"
    assert NET_BASE_MIN >= 0, "net-base floor must be ≥ 0"
    assert 0 < WF_SIGN_FRAC <= 1, "wf sign frac must be in (0,1]"
    assert WF_MIN_WEEK_N > 0 and WF_MIN_WEEKS > 0, "wf week counts must be positive"
    assert 0 < EP_BAND_LO < EP_BAND_HI < 1, "ep band must be 0 < LO < HI < 1"
    assert WILSON_Z > 0, "wilson z must be > 0"

    print("gates: OK")
    print(f"  1-pred lens hurdles: nev≥{bt:.0%}, net_base>{NET_BASE_MIN:.0%} "
          f"(wr Wilson-shrunk z={WILSON_Z}), wf sign≥{WF_SIGN_FRAC:.0%} "
          f"over ≥{WF_MIN_WEEKS}wk (each n≥{WF_MIN_WEEK_N})")
    print(f"  fit-for-size ep band: [{EP_BAND_LO}, {EP_BAND_HI}] (even-money; longshot/favorite deferred)")
    print(f"  bt mining gate (cross-bucket V1 ∩ V2): nev > {bt:.1%}")
    print(f"  bt → paper drift haircut             : −{BT_TO_PAPER_DRIFT:.1%}")
    print(f"  → paper-implied minimum               : {paper_implied:.1%}")
    print(f"  paper → live gate                     : nev > {live_gate:.1%} AND t > {PAPER_TO_LIVE_T_STAT}")
    print(f"  paper KILL cap                        : n > {PAPER_TO_LIVE_CAP_N} OR weeks > {PAPER_TO_LIVE_CAP_WEEKS}")
