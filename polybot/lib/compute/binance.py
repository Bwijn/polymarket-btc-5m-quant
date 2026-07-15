"""Binance kline-derived features.

Source: Binance /klines 1m candles (open, high, low, close, volume, taker_buy_volume).
SSOT: mining scratch/research/features/binance.py delegates here.
"""
from __future__ import annotations
import math

import numpy as np
import pandas as pd

from .constants import BN_PRE_WINDOWS_S


def compute_bn_features(klines: pd.DataFrame, cs: int) -> dict:
    """Binance kline-derived features at cs.

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
                out[f'bn_cvd_pre_{w}'] = np.nan
            if w == 60:
                out['bn_vol_zscore_pre_60'] = np.nan
            if w in (300, 900):
                out[f'bn_hl_range_pre_{w}'] = np.nan
                out[f'bn_clv_pre_{w}'] = np.nan
                out[f'bn_cvd_chgdn_pre_{w}'] = np.nan
                out[f'bn_cvd_chgup_pre_{w}'] = np.nan
            if w == 900:
                out['bn_secs_since_low_900'] = np.nan
                out['bn_secs_since_high_900'] = np.nan
            continue

        close_now   = seg['close'].iloc[-1]
        close_start = seg['open'].iloc[0]
        out[f'bn_chg_pct_pre_{w}'] = (close_now - close_start) / close_start if close_start else np.nan

        if w in (300, 900, 1800):
            logret = np.log(seg['close'] / seg['close'].shift(1)).dropna()
            out[f'bn_rv_{w}'] = float(logret.std()) * math.sqrt(len(logret)) if len(logret) >= 2 else np.nan

        if w in (60, 300, 900):
            vol_sum = seg['volume'].sum()
            tb = seg['taker_buy_volume'].sum()
            out[f'bn_taker_buy_ratio_pre_{w}'] = tb / vol_sum if vol_sum > 0 else np.nan
            out[f'bn_cvd_pre_{w}'] = 2 * tb - vol_sum   # signed taker vol = buy − sell

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
            # close location value: close position within window range, [0,1]
            out[f'bn_clv_pre_{w}'] = (close_now - lo) / (hi - lo) if hi > lo else np.nan
            # CVD partitioned by sign(chg) (direction-pure divergence legs);
            # NaN leg = condition not met (chg NaN → both legs NaN)
            cvd, chg = out[f'bn_cvd_pre_{w}'], out[f'bn_chg_pct_pre_{w}']
            out[f'bn_cvd_chgdn_pre_{w}'] = cvd if chg < 0 else np.nan
            out[f'bn_cvd_chgup_pre_{w}'] = cvd if chg > 0 else np.nan

        if w == 900:
            # secs since most recent bar printing window extreme (re-touch restarts
            # the hold clock); bar-open anchored → {60,...,900} step 60
            lows, highs = seg['low'], seg['high']
            out['bn_secs_since_low_900'] = float(cs - lows[lows == lows.min()].index.max())
            out['bn_secs_since_high_900'] = float(cs - highs[highs == highs.max()].index.max())

    return out
