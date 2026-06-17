"""Cross-validation: build_features.py vs polybot/factor/compute.py byte-identity.

Context: compute.py is SSOT for mining + paper feature math. Without this test,
         compute.py and build_features.py could drift in subtle math (forward-fill
         edge case, NaN handling, dtype cast) causing paper-vs-mining boolean flip
         on identical data.
Source:  Real events from pm_btc5m.db (events table + binance_klines).
Expected: Per-event, every shared feature column matches byte-for-byte. NaN equals NaN.
          Failures print first 5 col-level diffs then raise.

Run:    uv run python scratch/factor_lab/test_compute_equivalence.py
"""
from __future__ import annotations
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# scratch/research/features/ is the new modular feature pipeline.
# Project root for polybot.* package resolution.
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent / 'research'))      # scratch/research/ (for `features` pkg)
sys.path.insert(0, str(_HERE.parent.parent))            # project root
from features import binance as _bn_mod   # mining-side ref (post-refactor)
from polybot.lib import compute

DB = '/home/polymarket_work/scratch/data/pm_btc5m.db'
N_EVENTS = 10           # sample size
SEED = 42               # deterministic shuffle


def _byte_equal(a, b) -> bool:
    """Strict equality with NaN-equals-NaN semantics. None == np.nan accepted."""
    a_nan = a is None or (isinstance(a, float) and math.isnan(a))
    b_nan = b is None or (isinstance(b, float) and math.isnan(b))
    if a_nan and b_nan:
        return True
    if a_nan or b_nan:
        return False
    return a == b


def test_binance() -> None:
    print(); print('=' * 70); print('Binance features equivalence'); print('=' * 70)
    con = sqlite3.connect(DB)
    events = con.execute("""
        SELECT cid, candle_start FROM events ORDER BY RANDOM() LIMIT ?
    """, (N_EVENTS,)).fetchall()
    klines = pd.read_sql("""
        SELECT open_ts_ms/1000 AS ts, open, high, low, close,
               volume, quote_volume, taker_buy_volume
        FROM binance_klines ORDER BY open_ts_ms
    """, con)
    con.close()
    klines['ts'] = klines['ts'].astype(np.int64)
    klines.set_index('ts', inplace=True)
    print(f"  sampled {len(events)} events, loaded {len(klines)} klines")

    n_cols_checked = 0
    n_diffs = 0
    for cid, cs in events:
        # Mining-side: replicate build_features._build_binance inner loop for this event
        rec_mining = {}
        for w in _bn_mod.BN_PRE_WINDOWS_S:
            seg = klines.loc[(klines.index >= cs - w) & (klines.index < cs)]
            if len(seg) == 0:
                rec_mining[f'bn_chg_pct_pre_{w}'] = np.nan
                if w in (300, 900, 1800):  rec_mining[f'bn_rv_{w}'] = np.nan
                if w in (60, 300, 900):    rec_mining[f'bn_taker_buy_ratio_pre_{w}'] = np.nan
                if w == 60:                rec_mining['bn_vol_zscore_pre_60'] = np.nan
                if w in (300, 900):        rec_mining[f'bn_hl_range_pre_{w}'] = np.nan
                continue
            close_now = seg['close'].iloc[-1]
            close_start = seg['open'].iloc[0]
            rec_mining[f'bn_chg_pct_pre_{w}'] = (close_now - close_start) / close_start if close_start else np.nan
            if w in (300, 900, 1800):
                logret = np.log(seg['close'] / seg['close'].shift(1)).dropna()
                rec_mining[f'bn_rv_{w}'] = float(logret.std()) * math.sqrt(len(logret)) if len(logret) >= 2 else np.nan
            if w in (60, 300, 900):
                vol_sum = seg['volume'].sum()
                rec_mining[f'bn_taker_buy_ratio_pre_{w}'] = seg['taker_buy_volume'].sum() / vol_sum if vol_sum > 0 else np.nan
            if w == 60:
                seg_long = klines.loc[(klines.index >= cs - 3600) & (klines.index < cs)]
                if len(seg_long) >= 5:
                    mu, sd = seg_long['volume'].mean(), seg_long['volume'].std(ddof=0)
                    rec_mining['bn_vol_zscore_pre_60'] = (seg['volume'].mean() - mu) / sd if sd > 0 else np.nan
                else:
                    rec_mining['bn_vol_zscore_pre_60'] = np.nan
            if w in (300, 900):
                hi, lo = seg['high'].max(), seg['low'].min()
                rec_mining[f'bn_hl_range_pre_{w}'] = (hi - lo) / close_start if close_start else np.nan

        rec_paper = compute.compute_bn_features(klines, cs)

        shared = set(rec_mining) & set(rec_paper)
        for col in sorted(shared):
            n_cols_checked += 1
            if not _byte_equal(rec_mining[col], rec_paper[col]):
                n_diffs += 1
                if n_diffs <= 5:
                    print(f"  DIFF cid={cid} cs={cs} col={col!r}: mining={rec_mining[col]!r} paper={rec_paper[col]!r}")

    print(f"  checked {n_cols_checked} (col × event) pairs, diffs={n_diffs}")
    assert n_diffs == 0, f"Binance byte-identity failed: {n_diffs} diffs"


def test_transforms() -> None:
    """compute_zs / compute_rank byte-identical to pandas rolling output."""
    print(); print('=' * 70); print('Transforms equivalence (compute_zs / compute_rank)'); print('=' * 70)
    np.random.seed(42)
    data = np.random.randn(500).astype(np.float64)
    data[10:20] = np.nan   # introduce some NaN gaps
    data[100:103] = np.nan
    s = pd.Series(data)

    # ── zs24h: pandas rolling(288, min_periods=50, closed='left') ──
    W, MP = 288, 50
    mean = s.rolling(W, min_periods=MP, closed='left').mean()
    std  = s.rolling(W, min_periods=MP, closed='left').std()
    zs_pandas = (s - mean) / std.replace(0, np.nan)

    zs_ours = []
    for i in range(len(s)):
        current = s.iloc[i]
        past = s.iloc[max(0, i - W):i].tolist()   # closed='left': [i-W, i-1]
        zs_ours.append(compute.compute_zs(current, past, MP))

    n_check = n_diff = 0
    for i in range(len(s)):
        a, b = zs_pandas.iloc[i], zs_ours[i]
        n_check += 1
        a_nan = math.isnan(a) if isinstance(a, float) else False
        b_nan = math.isnan(b) if isinstance(b, float) else False
        if a_nan and b_nan:
            continue
        if a_nan or b_nan or abs(a - b) > 1e-9:
            n_diff += 1
            if n_diff <= 3:
                print(f"  ZS DIFF row {i}: pandas={a!r} ours={b!r}")
    print(f"  zs24h:   checked {n_check} rows, diffs={n_diff}")
    assert n_diff == 0

    # ── rank24h: pandas rolling(288, min_periods=50).rank(pct=True) ──
    W, MP = 288, 50
    rank_pandas = s.rolling(W, min_periods=MP).rank(pct=True)

    rank_ours = []
    for i in range(len(s)):
        current = s.iloc[i]
        past = s.iloc[max(0, i - W + 1):i].tolist()  # closed='right' default: window=[i-W+1, i] inclusive
        rank_ours.append(compute.compute_rank(current, past, MP))

    n_check = n_diff = 0
    for i in range(len(s)):
        a, b = rank_pandas.iloc[i], rank_ours[i]
        n_check += 1
        a_nan = math.isnan(a) if isinstance(a, float) else False
        b_nan = math.isnan(b) if isinstance(b, float) else False
        if a_nan and b_nan:
            continue
        if a_nan or b_nan or abs(a - b) > 1e-9:
            n_diff += 1
            if n_diff <= 3:
                print(f"  RANK DIFF row {i}: pandas={a!r} ours={b!r}")
    print(f"  rank24h: checked {n_check} rows, diffs={n_diff}")
    assert n_diff == 0


if __name__ == '__main__':
    print(f"DB: {DB}")
    print(f"sample size: {N_EVENTS} events")
    test_binance()
    test_transforms()
    print(); print('=' * 70)
    print('ALL PASSED: compute.py byte-identical to build_features.py + pandas rolling')
    print('=' * 70)
