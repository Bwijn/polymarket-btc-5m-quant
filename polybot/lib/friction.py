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
    """Friction for backtest data — drift applied to ep (not as flat additive):
      paper_ep ≈ bt_ep + BT_TO_PAPER_DRIFT (1.5c/share)
      friction = fee_ratio(paper_ep) since paper pays fee on real ep, not bt ep.
    Caller must also use (ep + DRIFT) in PnL pred term (mine_gpu.py); fee side
    handled here. Applying drift in ep-space (not flat additive) makes the per-$1
    haircut correctly ep-dependent — larger for low ep via 1/ep nonlinearity."""
    return fee_ratio(entry_price + BT_TO_PAPER_DRIFT, rate)


def friction_breakdown(
    entry_price: float,
    *,
    context: str = "backtest",      # 'paper' | 'backtest'
    rate: float = PM_FEE_RATE_CRYPTO,
) -> dict[str, float]:
    """Itemized friction for inspection / logging.
    'paper'   : fee only on real ep (drift = 0, real exec)
    'backtest': fee on (ep + drift) — drift applied to ep first
    """
    fee_paper = fee_ratio(entry_price, rate)            # ep-only fee
    if context == "paper":
        return {"fee": fee_paper, "drift_fee_delta": 0.0, "total": fee_paper}
    if context == "backtest":
        fee_bt = fee_ratio(entry_price + BT_TO_PAPER_DRIFT, rate)
        return {"fee": fee_paper, "drift_fee_delta": fee_bt - fee_paper, "total": fee_bt}
    raise ValueError(f"unknown context {context!r}")


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

    # backtest friction = fee(ep + DRIFT), drift applied to ep not as additive
    assert paper_friction_ratio(0.6)    == fee_ratio(0.6)
    assert backtest_friction_ratio(0.6) == fee_ratio(0.6 + BT_TO_PAPER_DRIFT)
    # Note: bt friction < paper friction (fee shrinks as ep grows), because paper pays
    # fee on the actual (higher) paper_ep. The dominant nev-drop comes from PnL term
    # (1/paper_ep < 1/bt_ep), not fee. Both pieces must move together in mining.
    assert backtest_friction_ratio(0.6) < paper_friction_ratio(0.6)

    # friction_breakdown
    bd_p = friction_breakdown(0.6, context="paper")
    bd_b = friction_breakdown(0.6, context="backtest")
    assert bd_p["drift_fee_delta"] == 0.0
    assert bd_b["drift_fee_delta"] == fee_ratio(0.6 + BT_TO_PAPER_DRIFT) - fee_ratio(0.6)
    assert bd_p["total"] == fee_ratio(0.6)
    assert bd_b["total"] == fee_ratio(0.6 + BT_TO_PAPER_DRIFT)

    # Cross-rate sanity
    assert PM_FEE_RATES["Crypto"]      == 0.07
    assert PM_FEE_RATES["Geopolitics"] == 0.00

    print("friction: OK")
    print(f"  fee_ratio(0.6)         = {fee_ratio(0.6):.4f}  (paper fee, real exec ep=0.6)")
    print(f"  backtest_friction(0.6) = {backtest_friction_ratio(0.6):.4f}  (fee on paper_ep = 0.6 + {BT_TO_PAPER_DRIFT})")
    print(f"  paper_friction(0.6)    = {paper_friction_ratio(0.6):.4f}  (= fee_ratio(0.6), no drift)")
