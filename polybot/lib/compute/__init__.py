"""Pure-math feature extraction. SSOT (single source of truth) for mining + scanner.

Mining (`scratch/research/features/`) and scanner runtime (`polybot/runtime/features.py`)
both import from here. Modular layout mirrors mining package structure:
    pm        — PRE features (ticks-based, fidelity=1m)
    pmtrades  — INTRA features (trades-based, sub-second, b3295eb migration)
    binance   — BN features (klines)
    basis     — cross-source PM × BN derived
    transforms — rolling z-score / rank stateless functions

Backward compat: this __init__ re-exports all public functions, so existing
`from polybot.lib.compute import compute_pm_features` works unchanged.

Drift policy: ALL math implementations live here. Mining + scanner must NEVER
re-implement parallel — always import + delegate.
"""
from __future__ import annotations

# Re-export constants
from .constants import (
    PRE_OFFSETS, PRE_WINDOWS,
    BN_PRE_WINDOWS_S,
    BASIS_K,
    TRANSFORM_SPEC,
    PMT_ENTRY_GRID, PMT_INTRA_WINDOWS, PMT_FLOW_IMB_X,
    PMT_SPREAD_X, PMT_SPREAD_W, PMT_ENTRY_W, PMT_LARGE_SIZE,
    _TS, _SIDE, _PRICE, _SIZE, _ASSET, _PROXY,
)

# Re-export compute functions
from .pm import forward_fill_grid, stats_window, _pm_one_side, compute_pm_features
from .binance import compute_bn_features
from .basis import compute_basis_features
from .pmtrades import (
    _pmt_entry_prices, _pmt_window_stats, _pmt_chg_rate,
    _pmt_flow_imbalance, _pmt_impact, _pmt_velocity_burst_quiet,
    _pmt_whale, _pmt_wallets, _pmt_spread_proxy, _pmt_cross_token,
    _pmt_coarse_retained, _pmt_nan_record,
    compute_pmtrades_features,
)
from .transforms import compute_zs, compute_rank, parse_transform_col


# ── package-level self-test: smoke across all modules ───────────────────────
if __name__ == '__main__':
    import math
    from datetime import datetime, timezone
    import numpy as np

    # 1) forward_fill_grid + stats_window
    g = forward_fill_grid([{'t': 5, 'p': 0.5}, {'t': 8, 'p': 0.6}], 0, 10)
    assert g[5] == 0.5 and g[7] == 0.5 and g[8] == 0.6
    s = stats_window(np.array([1.0, 2.0, 3.0, 4.0, 5.0]), 0, 4)
    assert s['mean'] == 3.0 and abs(s['slope'] - 1.0) < 1e-9

    # 2) compute_pm_features
    cs = 1771027200  # 2026-02-14 Saturday
    ticks_up = [{'t': cs - 60, 'p': 0.48}, {'t': cs, 'p': 0.5}]
    ticks_dn = [{'t': cs - 60, 'p': 0.52}, {'t': cs, 'p': 0.5}]
    out = compute_pm_features(ticks_up, ticks_dn, cs)
    assert out['p_open_up'] == 0.5 and out['is_saturday'] == 1
    assert 'p_intra_60_up' not in out, "INTRA moved to pmtrades"

    # 3) compute_pmtrades_features
    UP_TOK, DN_TOK = 'TOK_UP', 'TOK_DN'
    rows = [
        (cs - 30,  'BUY', 0.49, 10.0, UP_TOK, 'wallet_a'),
        (cs,        'BUY', 0.50, 5.0,  UP_TOK, 'wallet_b'),
        (cs + 60,  'BUY', 0.51, 8.0,  UP_TOK, 'wallet_a'),
        (cs + 90,  'BUY', 0.52, 3.0,  UP_TOK, 'wallet_c'),
    ]
    pmt = compute_pmtrades_features(rows, cs, UP_TOK, DN_TOK)
    assert pmt['p_intra_60_up'] == 0.51 and pmt['p_intra_90_up'] == 0.52
    assert 'p_intra_90_up' in _pmt_nan_record()

    # 4) compute_basis_features
    pm = {'p_pre_60_up': 0.55, 'p_pre_60_dn': 0.45}
    bn = {'bn_chg_pct_pre_60': 0.002, 'bn_rv_300': 0.01}
    b = compute_basis_features(pm, bn)
    assert abs(b['basis_pre_60_up'] - (-0.05)) < 1e-9
    b_nan = compute_basis_features(pm, {'bn_chg_pct_pre_60': 0.002, 'bn_rv_300': float('nan')})
    assert math.isnan(b_nan['basis_pre_60_up'])

    # 5) compute_zs / compute_rank / parse_transform_col
    z = compute_zs(5.0, [1, 2, 3, 4], min_periods=3)
    assert abs(z - 1.9364916731037083) < 1e-9
    r = compute_rank(2.0, [1.0, 2.0, 3.0, 2.0], min_periods=3)
    assert abs(r - 0.6) < 1e-9
    assert parse_transform_col('delta_intra_60_dn__zs24h') == \
        ('delta_intra_60_dn', TRANSFORM_SPEC['__zs24h'])
    assert parse_transform_col('p_intra_60_up') is None

    print("polybot.lib.compute (package): self-test PASS")
