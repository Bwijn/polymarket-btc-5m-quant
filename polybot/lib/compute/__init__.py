"""Pure-math feature extraction. SSOT (single source of truth) for mining + scanner.

Mining (`scratch/research/features/`) and scanner runtime (`polybot/runtime/features.py`)
both import from here. Modular layout mirrors mining package structure:
    pmtrades  — EP sampling (trades-based, p_intra_X + staleness)
    binance   — BN features (klines)
    transforms — rolling z-score / rank stateless functions

Re-exports all public functions, so `from polybot.lib.compute import
compute_pmtrades_features` works unchanged.

Drift policy: ALL math implementations live here. Mining + scanner must NEVER
re-implement parallel — always import + delegate.
"""
from __future__ import annotations

# Re-export constants
from .constants import (
    BN_PRE_WINDOWS_S,
    TRANSFORM_SPEC,
    PMT_ENTRY_GRID, PMT_ENTRY_W,
    _TS, _SIDE, _PRICE, _SIZE, _ASSET, _PROXY,
)

# Re-export compute functions
from .binance import compute_bn_features
from .pmtrades import (
    _pmt_entry_prices, _pmt_nan_record, compute_pmtrades_features,
)
from .transforms import compute_zs, compute_rank, parse_transform_col


# ── package-level self-test: smoke across all modules ───────────────────────
if __name__ == '__main__':
    cs = 1771027200  # 2026-02-14 Saturday

    # 1) compute_pmtrades_features
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

    # 2) compute_zs / compute_rank / parse_transform_col
    z = compute_zs(5.0, [1, 2, 3, 4], min_periods=3)
    assert abs(z - 1.9364916731037083) < 1e-9
    r = compute_rank(2.0, [1.0, 2.0, 3.0, 2.0], min_periods=3)
    assert abs(r - 0.6) < 1e-9
    assert parse_transform_col('p_intra_60_up__zs24h') == \
        ('p_intra_60_up', TRANSFORM_SPEC['__zs24h'])
    assert parse_transform_col('p_intra_60_up') is None

    print("polybot.lib.compute (package): self-test PASS")
