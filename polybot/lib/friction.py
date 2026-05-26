"""PM friction model SSOT — shared by polybot scanner + mining + analysis.

  - Component 1: PM taker fee (precise, formula-based)
  - Component 2: bt → paper drift (empirical, see polybot/lib/gates.py)
                 only applied in backtest context (paper IS the real exec).

Anywhere else that needs friction (view, scanner.settle_one, mining net_ev,
analysis SQL constants), MUST reference this file. Don't hardcode 0.07
anywhere else — `grep` periodically and refactor to import.

Why one module not multiple constants scattered:
  - PM rate changed before (0.072 → 0.07) — caught only by chance probe.
  - view used 0.015 (stale) while SPEC said 5% (corrected) — drifted silently.
  - Single source = one edit point + one self-test = one truth.

References:
  - PM fee docs: https://docs.polymarket.com/trading/fees.md (verified 2026-05-10)
  - mechanics: fee = C × feeRate × p × (1-p), taker only, paid at match time
  - per-investment: fee_ratio = feeRate × (1 - entry_price), paid every trade
"""
from __future__ import annotations


# ============================================================================
# Component 1: PM taker fee (precise, formula-based)
# ============================================================================
# Source: gamma.feeSchedule.rate per feeType (verified via probe_pm_fee_real_v3).
# Polybot trades BTC up/down 5m → "Crypto" feeType.

PM_FEE_RATES: dict[str, float] = {
    "Crypto":      0.07,    # ← polybot default
    "Sports":      0.03,
    "Finance":     0.04,    # also Politics / Mentions / Tech
    "Politics":    0.04,
    "Mentions":    0.04,
    "Tech":        0.04,
    "Economics":   0.05,    # also Culture / Weather / Other / General
    "Culture":     0.05,
    "Weather":     0.05,
    "Other":       0.05,
    "General":     0.05,
    "Geopolitics": 0.00,
}
PM_FEE_RATE_CRYPTO = PM_FEE_RATES["Crypto"]


def fee_ratio(entry_price: float, rate: float = PM_FEE_RATE_CRYPTO) -> float:
    """PM taker fee per $1 invested. Paid at match time on EVERY taker fill
    (win or lose), independent of outcome. Maker pays 0.

    Returns: rate × (1 - entry_price)

    Use this directly to deflate paper data:
        net_pnl_ratio = pnl_ratio - fee_ratio(entry_price_paper)
    """
    return rate * (1.0 - entry_price)


# ============================================================================
# Component 2: bt → paper drift (empirical, applied only in backtest context)
# ============================================================================
# Sourced from polybot/lib/gates.py BT_TO_PAPER_DRIFT (measured 2026-05-25 on
# R2 / R4 / H5 paper trades, conservative round up). Paper = actual exec, no
# drift; bt = estimate, drift haircuts toward paper-implied.
#
# History (git audit, not for code reference):
# - Pre-2026-05-24: bt friction = fee + spread (mid-price era, +1.5% spread cross)
# - 2026-05-24: trade-based ep → bt friction = fee only (spread embedded in ask)
# - 2026-05-25: drift measured → bt friction = fee + drift (paper-implied)

from polybot.lib.gates import BT_TO_PAPER_DRIFT


def paper_friction_ratio(
    entry_price: float,
    rate: float = PM_FEE_RATE_CRYPTO,
) -> float:
    """Friction for paper data (entry_price_paper = book_ask, real exec price).
    Spread embedded in book_ask, no drift (this IS the actual paid price) → fee only.
    """
    return fee_ratio(entry_price, rate)


def backtest_friction_ratio(
    entry_price: float,
    rate: float = PM_FEE_RATE_CRYPTO,
) -> float:
    """Friction for backtest data (entry_price_backtest = trade-based ep estimate).
    = fee + BT_TO_PAPER_DRIFT (empirical haircut for paper paying more than bt expected).

    Caller intent: subtract this from gev to get nev that approximates paper EV.
    Equivalent to: bt nev = bt gev − fee − drift
                 ≈ paper-implied nev (after drift correction)
    """
    return fee_ratio(entry_price, rate) + BT_TO_PAPER_DRIFT


def friction_breakdown(
    entry_price: float,
    *,
    context: str = "backtest",      # 'paper' | 'backtest'
    rate: float = PM_FEE_RATE_CRYPTO,
) -> dict[str, float]:
    """Itemized friction for inspection / logging.
    'paper'   : fee only (real exec, drift = 0)
    'backtest': fee + drift (bt estimate, drift haircuts toward paper-implied)
    """
    fee = fee_ratio(entry_price, rate)
    if context == "paper":
        drift = 0.0
    elif context == "backtest":
        drift = BT_TO_PAPER_DRIFT
    else:
        raise ValueError(f"unknown context {context!r}")
    return {"fee": fee, "drift": drift, "total": fee + drift}


# ============================================================================
# Self-test
# ============================================================================
if __name__ == "__main__":
    # fee_ratio: PM crypto formula
    assert abs(fee_ratio(0.6) - 0.028) < 1e-9, "fee at p=0.6 should be 2.8%"
    assert abs(fee_ratio(0.5) - 0.035) < 1e-9, "fee max @ p=0.5 = 3.5%"
    assert abs(fee_ratio(0.0) - 0.07)  < 1e-9, "fee at p=0 = full rate"
    assert abs(fee_ratio(1.0) - 0.0)   < 1e-9, "fee at p=1 = 0 (no payoff)"
    assert abs(fee_ratio(0.3) - fee_ratio(0.7) * (0.7/0.3)) > 0     # asymmetric in ratio terms
    # but symmetric in absolute USDC: 0.07 × 0.3 × 0.7 == 0.07 × 0.7 × 0.3 ✓ inherent in formula

    # 2026-05-25: bt friction = fee + DRIFT (paper-implied), paper = fee only (real exec)
    assert paper_friction_ratio(0.6)    == fee_ratio(0.6)
    assert backtest_friction_ratio(0.6) == fee_ratio(0.6) + BT_TO_PAPER_DRIFT
    assert backtest_friction_ratio(0.6) - paper_friction_ratio(0.6) == BT_TO_PAPER_DRIFT

    # friction_breakdown
    bd_p = friction_breakdown(0.6, context="paper")
    bd_b = friction_breakdown(0.6, context="backtest")
    assert bd_p["drift"] == 0.0
    assert bd_b["drift"] == BT_TO_PAPER_DRIFT
    assert bd_p["fee"] == bd_b["fee"] == fee_ratio(0.6)
    assert bd_b["total"] == bd_p["total"] + BT_TO_PAPER_DRIFT

    # Cross-rate sanity
    assert PM_FEE_RATES["Crypto"]      == 0.07
    assert PM_FEE_RATES["Geopolitics"] == 0.00

    print("friction: OK")
    print(f"  fee_ratio(0.6)         = {fee_ratio(0.6):.4f}  (2.8%)")
    print(f"  paper_friction(0.6)    = {paper_friction_ratio(0.6):.4f}  (fee only, real exec)")
    print(f"  backtest_friction(0.6) = {backtest_friction_ratio(0.6):.4f}  (fee + drift {BT_TO_PAPER_DRIFT:.1%} → paper-implied)")
