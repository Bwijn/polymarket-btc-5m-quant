"""PM features (PRE only — INTRA migrated to pmtrades 2026-05-24, b3295eb).

Data source: PM /prices-history mid-price ticks (fidelity=1m, forward-filled to 1s).
PRE window 1h-6h, 60+ points/window → mid-price aggregation robust.

SSOT: mining scratch/research/features/pm.py delegates here. Scanner runtime
features.py compute_row 也 call here. Math 唯一一份.
"""
from __future__ import annotations
import math
from datetime import datetime, timezone

import numpy as np

from .constants import PRE_OFFSETS, PRE_WINDOWS


def forward_fill_grid(points: list[dict], t_min: int, t_max: int) -> np.ndarray:
    """Resample list-of-{t,p} to 1-second grid via forward-fill.
    NaN before first observed point. Length = t_max - t_min + 1."""
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
    """Stats over grid[idx_lo:idx_hi+1]. Drop NaN. NaN if <2 points."""
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


def _pm_one_side(cs: int, points: list[dict], side: str) -> dict:
    """All PRE-only PM features for one side. INTRA cols moved to pmtrades."""
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

    # deltas (PRE only)
    for off in PRE_OFFSETS:
        if off == 0:
            continue
        p_at = out[f'p_pre_{off}{sfx}']
        out[f'delta_pre_{off}{sfx}'] = p_open - p_at if not math.isnan(p_at) else np.nan

    # pre window stats (mean / std / rng / slope)
    for w in PRE_WINDOWS:
        lo = max(0, idx_cs - w)
        hi = idx_cs
        s = stats_window(grid, lo, hi)
        out[f'mean_pre_{w}{sfx}']  = s['mean']
        out[f'std_pre_{w}{sfx}']   = s['std']
        out[f'rng_pre_{w}{sfx}']   = s['rng']
        out[f'slope_pre_{w}{sfx}'] = s['slope']

    # chg_rate PRE only: count of price changes per minute
    for w in [900, 1800, 3600]:
        lo = max(0, idx_cs - w)
        seg = grid[lo:idx_cs + 1]
        seg = seg[~np.isnan(seg)]
        n_changes = (np.diff(seg) != 0).sum() if len(seg) > 1 else 0
        out[f'chg_rate_pre_{w}{sfx}'] = float(n_changes) / (w / 60)

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


def compute_pm_features(ticks_up: list[dict], ticks_dn: list[dict], cs: int) -> dict:
    """Full PM PRE feature dict at candle_start cs. Combines both sides + time features."""
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
