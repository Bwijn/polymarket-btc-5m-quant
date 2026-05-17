"""PM friction model SSOT — shared by polybot scanner + mining + analysis.

Two structural costs only. No "latency / staleness buffer" — backtest is
idealized by definition, paper's purpose is to MEASURE drift empirically.
Adding a buffer to backtest = double-counting against paper's own measurement.

  - Component 1: PM taker fee (precise, formula-based)
  - Component 2: Spread cross (taker pays ask, backtest assumes mid)

Anywhere else that needs friction (view, scanner.settle_one, mining net_ev,
analysis SQL constants), MUST reference this file. Don't hardcode 0.07 /
0.015 anywhere else — `grep` for those values periodically; if found
elsewhere, refactor to import from here.

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
# Component 2: Spread cross (theoretical, taker pays ask not mid)
# ============================================================================
# Constant estimate. Real spread varies by market liquidity but our paper
# data on btc-updown-5m shows roughly 0.01-0.03 typical, with wider tails.
# Used by backtest only — paper's entry_price_paper = book_ask already
# embeds spread, so paper friction does NOT add this.

SPREAD_CROSS_RATIO = 0.015


# ============================================================================
# Composers — context-aware total friction
# ============================================================================

def paper_friction_ratio(
    entry_price: float,
    rate: float = PM_FEE_RATE_CRYPTO,
) -> float:
    """Friction for paper data (entry_price_paper = book_ask).

    Spread is ALREADY embedded in book_ask. So paper friction = fee only.
    """
    return fee_ratio(entry_price, rate)


def backtest_friction_ratio(
    entry_price: float,
    rate: float = PM_FEE_RATE_CRYPTO,
) -> float:
    """Friction for backtest data (entry_price_backtest = prices-history mid).

    Includes fee + spread (backtest's mid doesn't embed spread cost).
    No "latency / staleness buffer" — backtest is idealized by design,
    paper measures drift empirically.
    """
    return fee_ratio(entry_price, rate) + SPREAD_CROSS_RATIO


def friction_breakdown(
    entry_price: float,
    *,
    context: str = "backtest",      # 'paper' | 'backtest'
    rate: float = PM_FEE_RATE_CRYPTO,
) -> dict[str, float]:
    """Itemized friction for inspection / logging."""
    fee = fee_ratio(entry_price, rate)
    if context == "paper":
        spread = 0.0
    elif context == "backtest":
        spread = SPREAD_CROSS_RATIO
    else:
        raise ValueError(f"unknown context {context!r}")
    return {"fee": fee, "spread": spread, "total": fee + spread}


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

    # paper_friction_ratio: only fee
    assert paper_friction_ratio(0.6) == fee_ratio(0.6)

    # backtest_friction_ratio: fee + spread (no latency buffer)
    assert abs(backtest_friction_ratio(0.6) - 0.043) < 1e-9     # 2.8% + 1.5%

    # friction_breakdown context branches
    bd_p = friction_breakdown(0.6, context="paper")
    bd_b = friction_breakdown(0.6, context="backtest")
    assert bd_p["spread"] == 0.0
    assert bd_b["spread"] == SPREAD_CROSS_RATIO
    assert bd_p["fee"] == bd_b["fee"]                   # fee identical across contexts
    assert bd_p["total"] < bd_b["total"]                # paper has less friction (spread already in book_ask)

    # Cross-rate sanity
    assert PM_FEE_RATES["Crypto"]      == 0.07
    assert PM_FEE_RATES["Geopolitics"] == 0.00

    print("friction: OK")
    print(f"  fee_ratio(0.6)         = {fee_ratio(0.6):.4f}  (2.8%)")
    print(f"  fee_ratio(0.5)         = {fee_ratio(0.5):.4f}  (3.5%, max)")
    print(f"  paper_friction(0.6)    = {paper_friction_ratio(0.6):.4f}      (fee only)")
    print(f"  backtest_friction(0.6) = {backtest_friction_ratio(0.6):.4f}      (fee + spread)")
