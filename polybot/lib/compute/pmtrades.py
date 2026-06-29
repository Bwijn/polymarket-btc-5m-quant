"""PM trades → EP (entry-price) sampling.

Data source: PM /trades (sub-second resolution). Trades are retained ONLY for EP
measurement — `p_intra_X` = last BUY in [cs+X−5, cs+X] (5s window = PMT_ENTRY_W,
cap-immune: the most recent print is what the 4000-cap keeps). No stale fallback
(2026-06-25): empty window → NaN, so `staleness_s` is always ≤5; NaN = no BUY in
window. All flow/microstructure generators removed 2026-06-17:
window aggregates carry 4k-cap selection bias, and intra signals failed bt.

SSOT: mining scratch/research/features/pmtrades.py delegates here.
Scanner runtime features.py compute_row pulls raw trades via ws TradesCache, then
calls here.
"""
from __future__ import annotations
import numpy as np

from .constants import PMT_ENTRY_GRID, PMT_ENTRY_W, _TS, _SIDE, _PRICE, _ASSET


def _pmt_entry_prices(rows: list, cs: int, up_tok: str, dn_tok: str) -> dict:
    """EP snapshot = last BUY in [target-PMT_ENTRY_W, target] for each entry_t X.
    Past-only (P4 fix 2026-05-26): mining DB 见未来 trade vs scanner TradesCache 只见
    过去 trade → 同 SSOT fn 喂不同 input shape 会天然 drift. past-only 让两侧对齐.
    No fallback (2026-06-25): 窗内无 BUY → NaN. 旧 fallback (往回抓任意旧 BUY) 产出
    staleness>5 陈旧 ep (尾盘赢家侧 0.99 token BUY 枯竭时尤甚), 污染 nev → 弃."""
    out = {}
    for sfx, tok in (('up', up_tok), ('dn', dn_tok)):
        buys = [r for r in rows if r[_SIDE] == 'BUY' and r[_ASSET] == tok]
        for X in PMT_ENTRY_GRID:
            target = cs + X
            cands = [r for r in buys if (target - PMT_ENTRY_W) <= r[_TS] <= target]
            if cands:
                nearest = min(cands, key=lambda r: abs(r[_TS] - target))
                p, stale = float(nearest[_PRICE]), float(target - nearest[_TS])
            else:
                p, stale = np.nan, np.nan
            out[f'p_intra_{X}_{sfx}'] = p
            out[f'p_intra_{X}_{sfx}_staleness_s'] = stale
    return out


def _pmt_nan_record() -> dict:
    """No-trade fallback: NaN for all EP output cols."""
    out = {}
    for X in PMT_ENTRY_GRID:
        for sfx in ('up', 'dn'):
            out[f'p_intra_{X}_{sfx}'] = np.nan
            out[f'p_intra_{X}_{sfx}_staleness_s'] = np.nan
    return out


def compute_pmtrades_features(rows: list, cs: int, up_token: str, dn_token: str) -> dict:
    """EP feature dict for one event.

    rows: list of (ts, side, price, size, asset, proxy_wallet) tuples from trades
          table for this cid in window [cs-300, cs+330]. Empty list = no trades:
          returns NaN fallback record.
    """
    if not rows:
        return _pmt_nan_record()
    return _pmt_entry_prices(rows, cs, up_token, dn_token)
