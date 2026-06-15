"""Shared constants for all compute families.

Centralizing here = single source of truth: mining + scanner runtime + tests
all reference the same windows, offsets, position indices. Drift impossible
at constant level — only at math level (and that's the broader package design).
"""
from __future__ import annotations

# ── Binance constants ────────────────────────────────────────────────────────
BN_PRE_WINDOWS_S = [60, 300, 900, 1800, 3600]

# ── Transform constants (regime-relative rolling) ────────────────────────────
# Window = event count (~5min spacing → 288 ≈ 24h, 2016 ≈ 7d).
# min_periods = mining 容忍下限 (50 ≈ ~4h coverage, 200 ≈ ~17h).
TRANSFORM_SPEC = {
    '__zs24h':   {'op': 'zs',   'window': 288,  'min_periods': 50},
    '__zs7d':    {'op': 'zs',   'window': 2016, 'min_periods': 200},
    '__rank24h': {'op': 'rank', 'window': 288,  'min_periods': 50},
}

# ── pmtrades constants (trades-based intra-candle) ───────────────────────────
PMT_ENTRY_GRID    = [30, 45, 60, 75, 90, 120, 150, 180, 240, 270]
PMT_INTRA_WINDOWS = [30, 60, 90, 120, 180, 240, 300]
PMT_FLOW_IMB_X    = [30, 60, 90, 120, 180, 240, 270]
PMT_SPREAD_X      = [30, 60, 90, 120]
PMT_SPREAD_W      = 15
PMT_ENTRY_W       = 5
PMT_LARGE_SIZE    = 500

# Row position indices for trades tuples (ts, side, price, size, asset, proxy_wallet).
_TS, _SIDE, _PRICE, _SIZE, _ASSET, _PROXY = 0, 1, 2, 3, 4, 5
