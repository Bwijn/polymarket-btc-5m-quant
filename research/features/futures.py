"""Binance futures meta (funding / OI / LS ratios) → _features_futures.parquet."""
from __future__ import annotations
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from ._common import DB, OUT_DIR


def build() -> Path:
    """Build _features_futures.parquet by looking up each event's cs against futures meta tables."""
    print(); print('=' * 70); print('futures.build: futures meta → _features_futures.parquet'); print('=' * 70)
    con = sqlite3.connect(DB)
    events = pd.read_sql("SELECT cid, candle_start AS cs FROM events ORDER BY candle_start", con)

    funding = pd.read_sql("SELECT funding_ts_ms/1000 AS ts, funding_rate FROM binance_funding_rate ORDER BY funding_ts_ms", con)
    oi      = pd.read_sql("SELECT ts_ms/1000 AS ts, sum_open_interest FROM binance_open_interest_hist ORDER BY ts_ms", con)
    top_acct = pd.read_sql("SELECT ts_ms/1000 AS ts, long_short_ratio FROM binance_top_ls_account_ratio ORDER BY ts_ms", con)
    top_pos  = pd.read_sql("SELECT ts_ms/1000 AS ts, long_short_ratio FROM binance_top_ls_position_ratio ORDER BY ts_ms", con)
    gl_acct  = pd.read_sql("SELECT ts_ms/1000 AS ts, long_short_ratio FROM binance_global_ls_account_ratio ORDER BY ts_ms", con)
    con.close()

    def asof_lookup(df_src: pd.DataFrame, col: str, ts_q: np.ndarray) -> np.ndarray:
        if len(df_src) == 0:
            return np.full(len(ts_q), np.nan)
        src_ts = df_src['ts'].values
        idx = np.searchsorted(src_ts, ts_q, side='right') - 1
        out = np.where(idx >= 0, df_src[col].values[np.clip(idx, 0, len(df_src)-1)], np.nan)
        return out

    ts_q = events['cs'].values
    out_df = pd.DataFrame({'cid': events['cid'], 'cs': events['cs']})
    out_df['fund_8h_now'] = asof_lookup(funding, 'funding_rate', ts_q)
    fund_24h_ago = asof_lookup(funding, 'funding_rate', ts_q - 86400)
    out_df['fund_chg_24h'] = out_df['fund_8h_now'] - fund_24h_ago

    oi_now = asof_lookup(oi, 'sum_open_interest', ts_q)
    oi_1h = asof_lookup(oi, 'sum_open_interest', ts_q - 3600)
    oi_4h = asof_lookup(oi, 'sum_open_interest', ts_q - 4*3600)
    out_df['oi_chg_pct_1h'] = (oi_now - oi_1h) / oi_1h
    out_df['oi_chg_pct_4h'] = (oi_now - oi_4h) / oi_4h

    out_df['ls_top_acct_ratio'] = asof_lookup(top_acct, 'long_short_ratio', ts_q)
    out_df['ls_top_pos_ratio']  = asof_lookup(top_pos,  'long_short_ratio', ts_q)
    out_df['ls_global_acct_ratio'] = asof_lookup(gl_acct, 'long_short_ratio', ts_q)

    out = OUT_DIR / '_features_futures.parquet'
    out_df.to_parquet(out, index=False, compression='zstd')
    print(f"  → {out} shape={out_df.shape}, size={out.stat().st_size/1024/1024:.2f} MB")
    return out
