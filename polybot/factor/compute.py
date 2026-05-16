"""Pure-math feature extraction. SSOT (single source of truth) for mining + paper.

Mining (`scratch/factor_lab/build_features.py`) and paper (`polybot/factor/features.py`)
both compute features. Without a shared module, the two implementations drift —
identical expr can return different boolean on identical data, breaking deploys.
This file is the shared math: pure functions, no I/O, no side effects.

Coverage (per current 7 paper_candidates):
    PM:        intra/pre points + window stats + chg_rate + z + derived (vol_ratio,
               slope_diff, asym, dist_fair_open) + time features
    Binance:   bn_chg_pct_pre_*, bn_rv_*, bn_taker_buy_ratio_pre_*, bn_vol_zscore_pre_60,
               bn_hl_range_pre_*
    basis:     basis_pre_W_up/dn (PM × Binance derived prob)

Excluded:
    pmtrades, futures: no current candidate uses these.
    transforms (__zs24h/__zs7d/__rank24h): stateful (rolling buffer), handled in
        paper-time runtime layer (next infra task).

Byte-identity guarantee: `scratch/factor_lab/test_compute_equivalence.py` asserts
this module's output equals `build_features.py` for the same input on real events.
"""
from __future__ import annotations
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

# ── constants: 必须跟 build_features.py 完全一致 ─────────────────────────────

PRE_OFFSETS   = [3600, 1800, 900, 600, 300, 120, 60, 30, 0]
INTRA_OFFSETS = [15, 30, 45, 60, 75, 90, 120, 150, 180, 240, 270]
PRE_WINDOWS   = [60, 180, 300, 600, 900, 1800, 3600]
INTRA_WINDOWS = [30, 60, 90, 120, 180, 240, 300]

BN_PRE_WINDOWS_S = [60, 300, 900, 1800, 3600]
BASIS_K = 2.0


# ── PM core helpers ──────────────────────────────────────────────────────────

def forward_fill_grid(points: list[dict], t_min: int, t_max: int) -> np.ndarray:
    """Resample list-of-{t,p} to 1-second grid via forward-fill.
    NaN before first observed point. Length = t_max - t_min + 1.
    Byte-identical to build_features.forward_fill_grid."""
    n = t_max - t_min + 1
    grid = np.full(n, np.nan, dtype=np.float64)
    if not points:
        return grid
    pts = sorted(points, key=lambda x: x['t'])
    pi = 0
    cur_p = np.nan
    for i in range(n):
        t = t_min + i
        while pi < len(pts) and pts[pi]['t'] <= t:
            cur_p = pts[pi]['p']
            pi += 1
        grid[i] = cur_p
    return grid


def stats_window(grid: np.ndarray, idx_lo: int, idx_hi: int) -> dict:
    """Stats over grid[idx_lo:idx_hi+1]. Drop NaN. NaN if <2 points.
    Byte-identical to build_features._stats_window."""
    seg = grid[idx_lo:idx_hi + 1]
    seg = seg[~np.isnan(seg)]
    if len(seg) < 2:
        return {'mean': np.nan, 'std': np.nan, 'rng': np.nan,
                'max': np.nan, 'min': np.nan, 'slope': np.nan}
    mean = float(seg.mean())
    std = float(seg.std(ddof=0))
    mn, mx = float(seg.min()), float(seg.max())
    rng = mx - mn
    x = np.arange(len(seg))
    slope = float(np.polyfit(x, seg, 1)[0])
    return {'mean': mean, 'std': std, 'rng': rng, 'max': mx, 'min': mn, 'slope': slope}


# ── PM features: one side (up or dn) ─────────────────────────────────────────

def _pm_one_side(cs: int, points: list[dict], side: str) -> dict:
    """All PM features for one side. Byte-identical to build_features._build_pm_one_side.
    Output dict has all column names suffixed with `_{side}`."""
    t_min, t_max = cs - 3600, cs + 360
    grid = forward_fill_grid(points, t_min, t_max)
    idx_cs = cs - t_min

    out = {}
    sfx = f'_{side}'

    # point lookups
    for off in PRE_OFFSETS:
        idx = idx_cs - off
        out[f'p_pre_{off}{sfx}'] = float(grid[idx]) if 0 <= idx < len(grid) else np.nan
    p_open = out[f'p_pre_0{sfx}']
    out[f'p_open{sfx}'] = p_open

    for off in INTRA_OFFSETS:
        idx = idx_cs + off
        out[f'p_intra_{off}{sfx}'] = float(grid[idx]) if 0 <= idx < len(grid) else np.nan

    # deltas
    for off in PRE_OFFSETS:
        if off == 0:
            continue
        p_at = out[f'p_pre_{off}{sfx}']
        out[f'delta_pre_{off}{sfx}'] = p_open - p_at if not math.isnan(p_at) else np.nan
    for off in INTRA_OFFSETS:
        p_at = out[f'p_intra_{off}{sfx}']
        out[f'delta_intra_{off}{sfx}'] = p_at - p_open if not math.isnan(p_at) else np.nan

    # pre window stats (mean / std / rng / slope)
    for w in PRE_WINDOWS:
        lo = max(0, idx_cs - w)
        hi = idx_cs
        s = stats_window(grid, lo, hi)
        out[f'mean_pre_{w}{sfx}']  = s['mean']
        out[f'std_pre_{w}{sfx}']   = s['std']
        out[f'rng_pre_{w}{sfx}']   = s['rng']
        out[f'slope_pre_{w}{sfx}'] = s['slope']

    # intra window stats (mean / std / rng / max / min)
    for w in INTRA_WINDOWS:
        lo = idx_cs
        hi = min(len(grid) - 1, idx_cs + w)
        s = stats_window(grid, lo, hi)
        out[f'mean_intra_{w}{sfx}'] = s['mean']
        out[f'std_intra_{w}{sfx}']  = s['std']
        out[f'rng_intra_{w}{sfx}']  = s['rng']
        out[f'max_intra_{w}{sfx}']  = s['max']
        out[f'min_intra_{w}{sfx}']  = s['min']

    # chg_rate: count of price changes per minute
    for w in [900, 1800, 3600]:
        lo = max(0, idx_cs - w)
        seg = grid[lo:idx_cs + 1]
        seg = seg[~np.isnan(seg)]
        n_changes = (np.diff(seg) != 0).sum() if len(seg) > 1 else 0
        out[f'chg_rate_pre_{w}{sfx}'] = float(n_changes) / (w / 60)
    for w in [120, 300]:
        hi = min(len(grid) - 1, idx_cs + w)
        seg = grid[idx_cs:hi + 1]
        seg = seg[~np.isnan(seg)]
        n_changes = (np.diff(seg) != 0).sum() if len(seg) > 1 else 0
        out[f'chg_rate_intra_{w}{sfx}'] = float(n_changes) / (w / 60)

    # z-scores
    for w in [300, 900, 1800, 3600]:
        mu = out.get(f'mean_pre_{w}{sfx}')
        sd = out.get(f'std_pre_{w}{sfx}')
        if mu is None or sd is None or math.isnan(mu) or math.isnan(sd) or sd == 0:
            out[f'z_pre_{w}{sfx}'] = np.nan
        else:
            out[f'z_pre_{w}{sfx}'] = (p_open - mu) / sd

    # vol_ratio
    s_pre_3600 = out.get(f'std_pre_3600{sfx}')
    s_pre_900  = out.get(f'std_pre_900{sfx}')
    out[f'vol_ratio_900_3600{sfx}'] = (s_pre_900 / s_pre_3600) \
        if (s_pre_3600 and s_pre_3600 != 0 and not math.isnan(s_pre_3600)
            and s_pre_900 and not math.isnan(s_pre_900)) else np.nan

    # slope_diff
    sl_300  = out.get(f'slope_pre_300{sfx}')
    sl_1800 = out.get(f'slope_pre_1800{sfx}')
    out[f'slope_diff_300_1800{sfx}'] = (sl_300 - sl_1800) \
        if (sl_300 is not None and sl_1800 is not None
            and not math.isnan(sl_300) and not math.isnan(sl_1800)) else np.nan

    # asymmetry over pre_3600
    mx_3600 = grid[max(0, idx_cs - 3600):idx_cs + 1]
    mx_3600 = mx_3600[~np.isnan(mx_3600)]
    if len(mx_3600) >= 2 and not math.isnan(p_open):
        upper = float(mx_3600.max()) - p_open
        lower = p_open - float(mx_3600.min())
        out[f'asym_3600{sfx}'] = upper - lower
    else:
        out[f'asym_3600{sfx}'] = np.nan

    # dist from fair coin
    out[f'dist_fair_open{sfx}'] = abs(p_open - 0.5) if not math.isnan(p_open) else np.nan

    return out


# ── PM features: both sides + time ───────────────────────────────────────────

def compute_pm_features(ticks_up: list[dict], ticks_dn: list[dict], cs: int) -> dict:
    """Full PM feature dict at candle_start cs. Combines both sides + time features.
    Mirrors build_features._build_pm per-event output (excluding cid/era/up_won meta).
    """
    out = {}
    out.update(_pm_one_side(cs, ticks_up, 'up'))
    out.update(_pm_one_side(cs, ticks_dn, 'dn'))

    utc = datetime.fromtimestamp(cs, tz=timezone.utc)
    out['dow']            = utc.weekday()
    out['hour_utc']       = utc.hour
    out['hour_bucket']    = utc.hour // 4
    out['minute_of_hour'] = utc.minute
    out['is_weekend']  = int(out['dow'] >= 5)
    out['is_friday']   = int(out['dow'] == 4)
    out['is_saturday'] = int(out['dow'] == 5)
    out['is_sunday']   = int(out['dow'] == 6)
    out['we_hour']     = out['is_weekend'] * out['hour_utc']
    return out


# ── Binance features ─────────────────────────────────────────────────────────

def compute_bn_features(klines: pd.DataFrame, cs: int) -> dict:
    """Binance kline-derived features at cs. Mirrors build_features._build_binance
    per-event output.

    klines: DataFrame indexed by ts (unix seconds at minute boundary), columns:
            open, high, low, close, volume, quote_volume, taker_buy_volume.
            Must cover at least [cs - 3600, cs).
    """
    out = {}
    for w in BN_PRE_WINDOWS_S:
        seg = klines.loc[(klines.index >= cs - w) & (klines.index < cs)]
        if len(seg) == 0:
            out[f'bn_chg_pct_pre_{w}'] = np.nan
            if w in (300, 900, 1800):
                out[f'bn_rv_{w}'] = np.nan
            if w in (60, 300, 900):
                out[f'bn_taker_buy_ratio_pre_{w}'] = np.nan
            if w == 60:
                out['bn_vol_zscore_pre_60'] = np.nan
            if w in (300, 900):
                out[f'bn_hl_range_pre_{w}'] = np.nan
            continue

        close_now   = seg['close'].iloc[-1]
        close_start = seg['open'].iloc[0]
        out[f'bn_chg_pct_pre_{w}'] = (close_now - close_start) / close_start if close_start else np.nan

        if w in (300, 900, 1800):
            logret = np.log(seg['close'] / seg['close'].shift(1)).dropna()
            out[f'bn_rv_{w}'] = float(logret.std()) * math.sqrt(len(logret)) if len(logret) >= 2 else np.nan

        if w in (60, 300, 900):
            vol_sum = seg['volume'].sum()
            out[f'bn_taker_buy_ratio_pre_{w}'] = seg['taker_buy_volume'].sum() / vol_sum if vol_sum > 0 else np.nan

        if w == 60:
            seg_long = klines.loc[(klines.index >= cs - 3600) & (klines.index < cs)]
            if len(seg_long) >= 5:
                mu, sd = seg_long['volume'].mean(), seg_long['volume'].std(ddof=0)
                out['bn_vol_zscore_pre_60'] = (seg['volume'].mean() - mu) / sd if sd > 0 else np.nan
            else:
                out['bn_vol_zscore_pre_60'] = np.nan

        if w in (300, 900):
            hi, lo = seg['high'].max(), seg['low'].min()
            out[f'bn_hl_range_pre_{w}'] = (hi - lo) / close_start if close_start else np.nan

    return out


# ── basis features (PM × Binance cross-source) ──────────────────────────────

def compute_basis_features(pm: dict, bn: dict) -> dict:
    """basis_pre_W_up/dn = p_pre_W - derived_prob_W. Mirrors build_features._build_basis
    per-event output. Requires both pm + bn dicts computed at same cs.

    derived_prob_up = clip(0.5 + chg_pct / (BASIS_K * rv), 0.001, 0.999)
        where rv uses bn_rv_W if W in (300, 900) else bn_rv_300.
    """
    out = {}
    for w in (60, 300, 900):
        chg = bn.get(f'bn_chg_pct_pre_{w}', float('nan'))
        rv_w = w if w in (300, 900) else 300
        rv = bn.get(f'bn_rv_{rv_w}', float('nan'))
        if rv == 0:
            rv = float('nan')

        if math.isnan(chg) or math.isnan(rv):
            derived = float('nan')
        else:
            derived = 0.5 + chg / (BASIS_K * rv)
            derived = max(0.001, min(0.999, derived))

        p_up = pm.get(f'p_pre_{w}_up', float('nan'))
        p_dn = pm.get(f'p_pre_{w}_dn', float('nan'))

        out[f'basis_pre_{w}_up'] = p_up - derived          # NaN propagates naturally
        out[f'basis_pre_{w}_dn'] = p_dn - (1 - derived)

    return out


# ── self-test: syntax + minimal sanity (real equivalence in tests/) ─────────

if __name__ == '__main__':
    # 1) forward_fill_grid: 单点 + 多点 + 空
    g = forward_fill_grid([{'t': 5, 'p': 0.5}, {'t': 8, 'p': 0.6}], 0, 10)
    assert len(g) == 11
    assert math.isnan(g[0])         # 5 之前无数据
    assert g[5] == 0.5
    assert g[7] == 0.5              # forward-fill
    assert g[8] == 0.6
    assert g[10] == 0.6
    assert len(forward_fill_grid([], 0, 5)) == 6
    assert all(math.isnan(v) for v in forward_fill_grid([], 0, 5))

    # 2) stats_window: 短窗 / 正常窗
    short = stats_window(np.array([1.0]), 0, 0)
    assert math.isnan(short['mean'])  # <2 points
    s = stats_window(np.array([1.0, 2.0, 3.0, 4.0, 5.0]), 0, 4)
    assert s['mean'] == 3.0
    assert s['min']  == 1.0
    assert s['max']  == 5.0
    assert s['rng']  == 4.0
    assert abs(s['slope'] - 1.0) < 1e-9

    # 3) compute_pm_features 出几个关键 col
    cs = 1771027200  # 2026-02-14 Saturday
    ticks_up = [{'t': cs, 'p': 0.5}, {'t': cs + 60, 'p': 0.42}, {'t': cs + 120, 'p': 0.45}]
    ticks_dn = [{'t': cs, 'p': 0.5}, {'t': cs + 60, 'p': 0.58}]
    out = compute_pm_features(ticks_up, ticks_dn, cs)
    assert out['p_intra_60_up'] == 0.42
    assert out['p_intra_60_dn'] == 0.58
    assert out['p_open_up'] == 0.5
    assert out['delta_intra_60_up'] == 0.42 - 0.5
    assert out['is_weekend'] == 1
    assert out['dow'] == 5  # Saturday
    assert out['is_saturday'] == 1

    # 4) compute_basis_features w/ NaN propagation
    pm = {'p_pre_60_up': 0.55, 'p_pre_60_dn': 0.45}
    bn = {'bn_chg_pct_pre_60': 0.002, 'bn_rv_300': 0.01}
    b = compute_basis_features(pm, bn)
    # derived = 0.5 + 0.002/(2*0.01) = 0.5 + 0.1 = 0.6, clip ok
    # basis_up = 0.55 - 0.6 = -0.05
    assert abs(b['basis_pre_60_up'] - (-0.05)) < 1e-9
    assert abs(b['basis_pre_60_dn'] - (0.45 - 0.4)) < 1e-9
    # NaN rv → basis NaN
    b_nan = compute_basis_features(pm, {'bn_chg_pct_pre_60': 0.002, 'bn_rv_300': float('nan')})
    assert math.isnan(b_nan['basis_pre_60_up'])

    print("compute: self-test OK (3 entry points + 2 helpers)")
