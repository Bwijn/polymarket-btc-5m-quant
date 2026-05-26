"""PM trades-based features (INTRA + whale/wallet/flow).

Data source: PM /trades (sub-second resolution). Replaces mid-price INTRA features
2026-05-24 (b3295eb migration) — mid 1m fidelity too coarse for 5min candle INTRA.

SSOT: mining scratch/research/features/pmtrades.py already delegates here.
Scanner runtime features.py compute_row 通过 ws TradesCache 拉 raw trades 后 call here.
"""
from __future__ import annotations
from collections import defaultdict

import numpy as np

from .constants import (
    PMT_ENTRY_GRID, PMT_INTRA_WINDOWS, PMT_FLOW_IMB_X, PMT_SPREAD_X,
    PMT_SPREAD_W, PMT_ENTRY_W, PMT_LARGE_SIZE,
    _TS, _SIDE, _PRICE, _SIZE, _ASSET, _PROXY,
)


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
    """Full pmtrades feature dict for one event.

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
