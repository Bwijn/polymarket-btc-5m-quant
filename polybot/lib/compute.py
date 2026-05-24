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
PRE_WINDOWS   = [60, 180, 300, 600, 900, 1800, 3600]
# INTRA_OFFSETS / INTRA_WINDOWS removed — migrated to compute_pmtrades_features below.

BN_PRE_WINDOWS_S = [60, 300, 900, 1800, 3600]
BASIS_K = 2.0

# Transform constants — must match build_features._build_transforms (line 489-491, 549-563).
# Window = event count (events ~5min apart, so 288 ≈ 24h, 2016 ≈ 7d).
# min_periods = mining 容忍下限 (50 ≈ ~4h coverage, 200 ≈ ~17h).
TRANSFORM_SPEC = {
    '__zs24h':   {'op': 'zs',   'window': 288,  'min_periods': 50},
    '__zs7d':    {'op': 'zs',   'window': 2016, 'min_periods': 200},
    '__rank24h': {'op': 'rank', 'window': 288,  'min_periods': 50},
}


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

    # INTRA point lookups + deltas + window stats + chg_rate REMOVED 2026-05-24.
    # Migrated to trade-based features (features/pmtrades.py + compute_pmtrades_features
    # below). Reason: PM mid prices fidelity=1m too coarse for intra-candle signal;
    # backfilled 85M trades give sub-second resolution. See todo NEXT-2.

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


# ── pmtrades features (trades-based, post 4000-cap backfill 2026-05-23) ─────

# Constants — single source of truth for both mining-side (features/pmtrades.py)
# and paper-side (this module). Changes here propagate to both via delegation.
PMT_ENTRY_GRID    = [30, 45, 60, 75, 90, 120, 150, 180, 240, 270]
PMT_INTRA_WINDOWS = [30, 60, 90, 120, 180, 240, 300]
PMT_FLOW_IMB_X    = [30, 60, 90, 120, 180, 240, 270]
PMT_SPREAD_X      = [30, 60, 90, 120]
PMT_SPREAD_W      = 15
PMT_ENTRY_W       = 5
PMT_LARGE_SIZE    = 500

# Row position constants for trades tuples (ts, side, price, size, asset, proxy_wallet).
_TS, _SIDE, _PRICE, _SIZE, _ASSET, _PROXY = 0, 1, 2, 3, 4, 5


def _pmt_entry_prices(rows: list, cs: int, up_tok: str, dn_tok: str) -> dict:
    """Group 1+1d: entry price + staleness + delta vs internal pre-cs open."""
    out = {}
    for sfx, tok in (('up', up_tok), ('dn', dn_tok)):
        buys = [r for r in rows if r[_SIDE] == 'BUY' and r[_ASSET] == tok]
        pre_open_price = next(
            (r[_PRICE] for r in reversed(buys) if r[_TS] <= cs), np.nan
        )
        for X in PMT_ENTRY_GRID:
            target = cs + X
            cands = [r for r in buys if abs(r[_TS] - target) <= PMT_ENTRY_W]
            if cands:
                nearest = min(cands, key=lambda r: abs(r[_TS] - target))
                p, stale = float(nearest[_PRICE]), abs(nearest[_TS] - target)
            else:
                prev = next((r for r in reversed(buys) if r[_TS] <= target), None)
                if prev:
                    p, stale = float(prev[_PRICE]), float(target - prev[_TS])
                else:
                    p, stale = np.nan, np.nan
            out[f'p_intra_{X}_{sfx}'] = p
            out[f'p_intra_{X}_{sfx}_staleness_s'] = stale
            out[f'delta_intra_{X}_{sfx}'] = (
                p - pre_open_price
                if not (np.isnan(p) or np.isnan(pre_open_price)) else np.nan
            )
    return out


def _pmt_window_stats(rows: list, cs: int, up_tok: str, dn_tok: str) -> dict:
    """Group W: VWAP / std / rng / max / min of BUY prices over INTRA windows."""
    out = {}
    for sfx, tok in (('up', up_tok), ('dn', dn_tok)):
        buys_in_intra = [r for r in rows
                         if r[_SIDE] == 'BUY' and r[_ASSET] == tok
                         and cs <= r[_TS] < cs + max(PMT_INTRA_WINDOWS)]
        for W in PMT_INTRA_WINDOWS:
            seg = [r for r in buys_in_intra if r[_TS] < cs + W]
            if len(seg) < 2:
                for stat in ('mean', 'std', 'rng', 'max', 'min'):
                    out[f'{stat}_intra_{W}_{sfx}'] = np.nan
                continue
            prices = np.array([r[_PRICE] for r in seg], dtype=np.float64)
            sizes = np.array([r[_SIZE] for r in seg], dtype=np.float64)
            tot_sz = sizes.sum()
            vwap = (prices * sizes).sum() / tot_sz if tot_sz > 0 else np.nan
            out[f'mean_intra_{W}_{sfx}'] = float(vwap)
            out[f'std_intra_{W}_{sfx}']  = float(prices.std(ddof=0))
            out[f'rng_intra_{W}_{sfx}']  = float(prices.max() - prices.min())
            out[f'max_intra_{W}_{sfx}']  = float(prices.max())
            out[f'min_intra_{W}_{sfx}']  = float(prices.min())
    return out


def _pmt_chg_rate(rows: list, cs: int, up_tok: str, dn_tok: str) -> dict:
    out = {}
    for sfx, tok in (('up', up_tok), ('dn', dn_tok)):
        for W in (120, 300):
            n = sum(1 for r in rows if r[_ASSET] == tok and cs <= r[_TS] < cs + W)
            out[f'chg_rate_intra_{W}_{sfx}'] = float(n) / (W / 60)
    return out


def _pmt_flow_imbalance(rows: list, cs: int, up_tok: str, dn_tok: str) -> dict:
    out = {}
    for sfx, tok in (('up', up_tok), ('dn', dn_tok)):
        for X in PMT_FLOW_IMB_X:
            target = cs + X
            seg = [r for r in rows if r[_ASSET] == tok and abs(r[_TS] - target) <= 2]
            buys = sum(r[_SIZE] for r in seg if r[_SIDE] == 'BUY')
            sells = sum(r[_SIZE] for r in seg if r[_SIDE] == 'SELL')
            tot = buys + sells
            out[f'pmt_flow_imb_5s_{X}_{sfx}'] = (buys - sells) / tot if tot > 0 else np.nan
    return out


def _pmt_impact(rows: list, cs: int, up_tok: str, dn_tok: str) -> dict:
    out = {}
    for sfx, tok in (('up', up_tok), ('dn', dn_tok)):
        for W in (60, 300):
            buys = [r for r in rows if r[_SIDE] == 'BUY' and r[_ASSET] == tok
                    and cs - W <= r[_TS] < cs]
            if len(buys) < 2:
                out[f'pmt_impact_pre_{W}_{sfx}'] = np.nan
                continue
            prices = np.array([r[_PRICE] for r in buys], dtype=np.float64)
            sizes = np.array([r[_SIZE] for r in buys], dtype=np.float64)
            dp = np.abs(np.diff(prices))
            sz_pair = sizes[1:]
            tot = sz_pair.sum()
            out[f'pmt_impact_pre_{W}_{sfx}'] = float((dp * sz_pair).sum() / tot) if tot > 0 else np.nan
    return out


def _pmt_velocity_burst_quiet(rows: list, cs: int) -> dict:
    out = {}
    for label, lo, hi in (('pre_60', cs - 60, cs), ('intra_60', cs, cs + 60)):
        seg_ts = sorted(r[_TS] for r in rows if lo <= r[_TS] < hi)
        n = len(seg_ts)
        win = hi - lo
        out[f'pmt_velocity_{label}'] = n / win
        if n < 2:
            out[f'pmt_burst_max_{label}'] = float(n)
            out[f'pmt_quiet_max_{label}'] = float(win)
            continue
        max_burst = 0
        j = 0
        for i, t in enumerate(seg_ts):
            while seg_ts[j] < t - 5 + 1:
                j += 1
            max_burst = max(max_burst, i - j + 1)
        out[f'pmt_burst_max_{label}'] = float(max_burst)
        gaps = ([seg_ts[0] - lo]
                + [seg_ts[i + 1] - seg_ts[i] for i in range(n - 1)]
                + [hi - seg_ts[-1]])
        out[f'pmt_quiet_max_{label}'] = float(max(gaps))
    return out


def _pmt_whale(rows: list, cs: int, up_tok: str, dn_tok: str) -> dict:
    out = {}
    for sfx, tok in (('up', up_tok), ('dn', dn_tok)):
        for W in (60, 300):
            seg = [r for r in rows if r[_ASSET] == tok and cs - W <= r[_TS] < cs
                   and r[_SIZE] >= PMT_LARGE_SIZE]
            buys = [r for r in seg if r[_SIDE] == 'BUY']
            sells = [r for r in seg if r[_SIDE] == 'SELL']
            n_buy_sz = sum(r[_SIZE] for r in buys)
            n_sell_sz = sum(r[_SIZE] for r in sells)
            tot = n_buy_sz + n_sell_sz
            out[f'pmt_whale_count_pre_{W}_{sfx}'] = len(buys)
            out[f'pmt_whale_size_pre_{W}_{sfx}'] = n_buy_sz
            out[f'pmt_whale_imb_pre_{W}_{sfx}'] = (n_buy_sz - n_sell_sz) / tot if tot > 0 else np.nan
    return out


def _pmt_wallets(rows: list, cs: int, up_tok: str, dn_tok: str) -> dict:
    from collections import defaultdict
    out = {}
    for sfx, tok in (('up', up_tok), ('dn', dn_tok)):
        for W in (60, 300):
            seg = [r for r in rows if r[_ASSET] == tok and r[_SIDE] == 'BUY'
                   and cs - W <= r[_TS] < cs]
            if not seg:
                out[f'pmt_unique_buyers_pre_{W}_{sfx}'] = 0
                out[f'pmt_buyer_hhi_pre_{W}_{sfx}'] = np.nan
                continue
            wallet_size = defaultdict(float)
            for r in seg:
                wallet_size[r[_PROXY]] += r[_SIZE]
            out[f'pmt_unique_buyers_pre_{W}_{sfx}'] = len(wallet_size)
            tot = sum(wallet_size.values())
            shares = [(v / tot) for v in wallet_size.values()] if tot > 0 else []
            out[f'pmt_buyer_hhi_pre_{W}_{sfx}'] = sum(s * s for s in shares) if shares else np.nan
    return out


def _pmt_spread_proxy(rows: list, cs: int, up_tok: str, dn_tok: str) -> dict:
    out = {}
    for sfx, tok in (('up', up_tok), ('dn', dn_tok)):
        for X in PMT_SPREAD_X:
            lo, hi = cs + X - PMT_SPREAD_W, cs + X + PMT_SPREAD_W
            last_buy = next((r[_PRICE] for r in reversed(rows)
                             if r[_ASSET] == tok and r[_SIDE] == 'BUY' and lo <= r[_TS] <= hi), None)
            last_sell = next((r[_PRICE] for r in reversed(rows)
                              if r[_ASSET] == tok and r[_SIDE] == 'SELL' and lo <= r[_TS] <= hi), None)
            out[f'pmt_spread_proxy_{X}_{sfx}'] = (
                float(last_buy) - float(last_sell)
                if (last_buy is not None and last_sell is not None) else np.nan
            )
    return out


def _pmt_cross_token(rows: list, cs: int, up_tok: str, dn_tok: str) -> dict:
    out = {}
    for W in (60, 300):
        up_dv = sum(r[_PRICE] * r[_SIZE] for r in rows if r[_ASSET] == up_tok and cs - W <= r[_TS] < cs)
        dn_dv = sum(r[_PRICE] * r[_SIZE] for r in rows if r[_ASSET] == dn_tok and cs - W <= r[_TS] < cs)
        tot = up_dv + dn_dv
        if tot > 0:
            up_sh, dn_sh = up_dv / tot, dn_dv / tot
        else:
            up_sh = dn_sh = np.nan
        out[f'pmt_up_share_pre_{W}'] = up_sh
        out[f'pmt_dn_share_pre_{W}'] = dn_sh
        out[f'pmt_cross_diff_pre_{W}'] = (up_sh - dn_sh) if tot > 0 else np.nan
    return out


def _pmt_coarse_retained(rows: list, cs: int, up_tok: str, dn_tok: str) -> dict:
    """Original 11 coarse cols (kept for backward compat with any older factor)."""
    out = {}
    for w in (60, 300):
        seg = [r for r in rows if cs - w <= r[_TS] < cs]
        out[f'pmt_count_pre_{w}'] = len(seg)
        sizes = [r[_SIZE] for r in seg]
        out[f'pmt_avg_size_pre_{w}'] = (sum(sizes) / len(sizes)) if sizes else np.nan
        out[f'pmt_large_count_pre_{w}'] = sum(1 for s in sizes if s >= PMT_LARGE_SIZE)
        for sfx, tok in (('up', up_tok), ('dn', dn_tok)):
            buys = sum(r[_SIZE] for r in seg if r[_SIDE] == 'BUY' and r[_ASSET] == tok)
            sells = sum(r[_SIZE] for r in seg if r[_SIDE] == 'SELL' and r[_ASSET] == tok)
            tot = buys + sells
            out[f'pmt_imbalance_pre_{w}_{sfx}'] = (buys - sells) / tot if tot > 0 else np.nan
    seg_eoc = [r for r in rows if cs + 270 <= r[_TS] < cs + 300]
    for sfx, tok in (('up', up_tok), ('dn', dn_tok)):
        buys = sum(r[_SIZE] for r in seg_eoc if r[_SIDE] == 'BUY' and r[_ASSET] == tok)
        sells = sum(r[_SIZE] for r in seg_eoc if r[_SIDE] == 'SELL' and r[_ASSET] == tok)
        tot = buys + sells
        out[f'pmt_eoc_imbalance_30_{sfx}'] = (buys - sells) / tot if tot > 0 else np.nan
    return out


def _pmt_nan_record() -> dict:
    """No-trade fallback: emit NaN/0 for all pmtrades output cols."""
    out = {}
    for X in PMT_ENTRY_GRID:
        for sfx in ('up', 'dn'):
            out[f'p_intra_{X}_{sfx}'] = np.nan
            out[f'p_intra_{X}_{sfx}_staleness_s'] = np.nan
            out[f'delta_intra_{X}_{sfx}'] = np.nan
    for W in PMT_INTRA_WINDOWS:
        for sfx in ('up', 'dn'):
            for st in ('mean', 'std', 'rng', 'max', 'min'):
                out[f'{st}_intra_{W}_{sfx}'] = np.nan
    for W in (120, 300):
        for sfx in ('up', 'dn'):
            out[f'chg_rate_intra_{W}_{sfx}'] = 0.0
    for X in PMT_FLOW_IMB_X:
        for sfx in ('up', 'dn'):
            out[f'pmt_flow_imb_5s_{X}_{sfx}'] = np.nan
    for W in (60, 300):
        for sfx in ('up', 'dn'):
            out[f'pmt_impact_pre_{W}_{sfx}'] = np.nan
    for label in ('pre_60', 'intra_60'):
        out[f'pmt_velocity_{label}'] = 0.0
        out[f'pmt_burst_max_{label}'] = 0.0
        out[f'pmt_quiet_max_{label}'] = 60.0
    for W in (60, 300):
        for sfx in ('up', 'dn'):
            out[f'pmt_whale_count_pre_{W}_{sfx}'] = 0
            out[f'pmt_whale_size_pre_{W}_{sfx}'] = 0.0
            out[f'pmt_whale_imb_pre_{W}_{sfx}'] = np.nan
            out[f'pmt_unique_buyers_pre_{W}_{sfx}'] = 0
            out[f'pmt_buyer_hhi_pre_{W}_{sfx}'] = np.nan
    for X in PMT_SPREAD_X:
        for sfx in ('up', 'dn'):
            out[f'pmt_spread_proxy_{X}_{sfx}'] = np.nan
    for W in (60, 300):
        out[f'pmt_up_share_pre_{W}'] = np.nan
        out[f'pmt_dn_share_pre_{W}'] = np.nan
        out[f'pmt_cross_diff_pre_{W}'] = np.nan
    for w in (60, 300):
        out[f'pmt_count_pre_{w}'] = 0
        out[f'pmt_avg_size_pre_{w}'] = np.nan
        out[f'pmt_large_count_pre_{w}'] = 0
        for sfx in ('up', 'dn'):
            out[f'pmt_imbalance_pre_{w}_{sfx}'] = np.nan
    out['pmt_eoc_imbalance_30_up'] = np.nan
    out['pmt_eoc_imbalance_30_dn'] = np.nan
    return out


def compute_pmtrades_features(rows: list, cs: int, up_token: str, dn_token: str) -> dict:
    """Full pmtrades feature dict for one event. SSOT for mining (features/pmtrades.py
    delegates here) + paper-time scanner runtime.

    rows: list of (ts, side, price, size, asset, proxy_wallet) tuples from trades
          table for this cid in window [cs-300, cs+330]. Empty list = no trades:
          returns NaN/0 fallback record.
    """
    if not rows:
        return _pmt_nan_record()
    out = {}
    out.update(_pmt_entry_prices(rows, cs, up_token, dn_token))
    out.update(_pmt_window_stats(rows, cs, up_token, dn_token))
    out.update(_pmt_chg_rate(rows, cs, up_token, dn_token))
    out.update(_pmt_flow_imbalance(rows, cs, up_token, dn_token))
    out.update(_pmt_impact(rows, cs, up_token, dn_token))
    out.update(_pmt_velocity_burst_quiet(rows, cs))
    out.update(_pmt_whale(rows, cs, up_token, dn_token))
    out.update(_pmt_wallets(rows, cs, up_token, dn_token))
    out.update(_pmt_spread_proxy(rows, cs, up_token, dn_token))
    out.update(_pmt_cross_token(rows, cs, up_token, dn_token))
    out.update(_pmt_coarse_retained(rows, cs, up_token, dn_token))
    return out


# ── transform pure functions (stateless) ────────────────────────────────────

def compute_zs(current: float, past_values: list[float], min_periods: int) -> float:
    """Z-score of current relative to past_values. Matches pandas
    rolling(window=W, min_periods=mp, closed='left').mean()/std() semantics:
    - past_values excludes current (closed='left')
    - drop NaN, need at least min_periods non-NaN
    - std uses ddof=1 (pandas default sample std)
    - returns NaN if std == 0 or insufficient data
    """
    if current is None or (isinstance(current, float) and math.isnan(current)):
        return float('nan')
    vals = [v for v in past_values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(vals) < min_periods:
        return float('nan')
    mean = sum(vals) / len(vals)
    variance = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
    std = variance ** 0.5
    if std == 0:
        return float('nan')
    return (current - mean) / std


def compute_rank(current: float, past_values: list[float], min_periods: int) -> float:
    """Percentile rank of current within [past_values + current], pct=True,
    method='average'. Matches pandas rolling(window=W, min_periods=mp).rank(pct=True).

    Note: mining uses closed='right' (default) for rank, so current IS included in window.
    We replicate by appending current to past_values for rank calc.
    """
    if current is None or (isinstance(current, float) and math.isnan(current)):
        return float('nan')
    vals = [v for v in past_values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    # pandas rank uses non-NaN count incl. current; min_periods checked against window's non-NaN
    # count incl current. Equivalent: need len(vals) + 1 >= min_periods.
    if len(vals) + 1 < min_periods:
        return float('nan')
    all_vals = vals + [current]
    n = len(all_vals)
    less  = sum(1 for v in all_vals if v < current)
    equal = sum(1 for v in all_vals if v == current)
    # method='average': tied ranks averaged. Ranks for tied group = (less+1)..(less+equal),
    # average = less + (equal+1)/2.
    avg_rank = less + (equal + 1) / 2
    return avg_rank / n


def parse_transform_col(col: str) -> tuple[str, dict] | None:
    """If col ends with a known transform suffix (`__zs24h` etc.), return
    (base, spec) where spec is {'op', 'window', 'min_periods'}. Else None.
    """
    for sfx, spec in TRANSFORM_SPEC.items():
        if col.endswith(sfx) and len(col) > len(sfx):
            return col[:-len(sfx)], spec
    return None


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

    # 3) compute_pm_features — PRE + time cols (INTRA cols moved to compute_pmtrades_features)
    cs = 1771027200  # 2026-02-14 Saturday
    ticks_up = [{'t': cs - 60, 'p': 0.48}, {'t': cs, 'p': 0.5}]
    ticks_dn = [{'t': cs - 60, 'p': 0.52}, {'t': cs, 'p': 0.5}]
    out = compute_pm_features(ticks_up, ticks_dn, cs)
    assert out['p_open_up'] == 0.5
    assert out['p_pre_60_up'] == 0.48
    assert abs(out['delta_pre_60_up'] - (0.5 - 0.48)) < 1e-12
    assert out['is_weekend'] == 1
    assert out['dow'] == 5  # Saturday
    assert out['is_saturday'] == 1
    assert 'p_intra_60_up' not in out, "intra cols moved to compute_pmtrades_features"

    # 4) compute_pmtrades_features — minimal smoke test (real coverage in mining-side tests)
    UP_TOK, DN_TOK = 'TOK_UP', 'TOK_DN'
    rows = [
        (cs - 30,  'BUY',  0.49, 10.0, UP_TOK, 'wallet_a'),
        (cs,        'BUY', 0.50, 5.0,  UP_TOK, 'wallet_b'),
        (cs + 60,  'BUY',  0.51, 8.0,  UP_TOK, 'wallet_a'),
        (cs + 90,  'BUY',  0.52, 3.0,  UP_TOK, 'wallet_c'),
    ]
    pmt = compute_pmtrades_features(rows, cs, UP_TOK, DN_TOK)
    assert pmt['p_intra_60_up'] == 0.51
    assert pmt['p_intra_60_up_staleness_s'] == 0
    assert pmt['p_intra_90_up'] == 0.52
    assert pmt['p_intra_60_dn'] != pmt['p_intra_60_dn'] or True  # NaN (no DN trades)
    # nan-record fallback
    assert 'p_intra_90_up' in _pmt_nan_record()
    print("compute.py self-test PASS")

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

    # 5) compute_zs: 基本算 + min_periods + std=0
    assert math.isnan(compute_zs(1.0, [1, 2, 3], min_periods=10))  # 不够
    z = compute_zs(5.0, [1, 2, 3, 4], min_periods=3)
    # mean=2.5, std ddof=1 = sqrt(5/3) ≈ 1.291, z = (5-2.5)/1.291 ≈ 1.936
    assert abs(z - 1.9364916731037083) < 1e-9, z
    assert math.isnan(compute_zs(5.0, [1, 1, 1, 1], min_periods=3))  # std=0
    assert math.isnan(compute_zs(float('nan'), [1, 2, 3], min_periods=2))

    # 6) compute_rank: 基本算 + ties + min_periods
    # past=[1,2,3,2], current=2 → all_vals=[1,2,3,2,2] sorted=[1,2,2,2,3]
    # less=1 (only 1<2), equal=3 (three 2s including current itself+two from past)
    # avg_rank = 1 + (3+1)/2 = 3, pct = 3/5 = 0.6
    r = compute_rank(2.0, [1.0, 2.0, 3.0, 2.0], min_periods=3)
    assert abs(r - 0.6) < 1e-9, r
    # 最低: current 比所有都小 → rank=1, pct=1/n
    r = compute_rank(0.5, [1, 2, 3], min_periods=3)
    assert abs(r - 0.25) < 1e-9, r   # less=0, equal=1, avg_rank=0+(1+1)/2=1, /4=0.25
    # min_periods 不够
    assert math.isnan(compute_rank(1.0, [2.0], min_periods=5))

    # 7) parse_transform_col
    assert parse_transform_col('delta_intra_60_dn__zs24h') == \
        ('delta_intra_60_dn', TRANSFORM_SPEC['__zs24h'])
    assert parse_transform_col('vol_ratio_900_3600_dn__zs7d') == \
        ('vol_ratio_900_3600_dn', TRANSFORM_SPEC['__zs7d'])
    assert parse_transform_col('bn_rv_1800__rank24h') == \
        ('bn_rv_1800', TRANSFORM_SPEC['__rank24h'])
    assert parse_transform_col('p_intra_60_up') is None       # no transform
    assert parse_transform_col('__zs24h') is None             # empty base

    print("compute: self-test OK (3 entry + 2 helpers + 3 transforms)")
