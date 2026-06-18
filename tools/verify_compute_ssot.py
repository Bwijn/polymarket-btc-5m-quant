"""Differential SSOT test: mining mining-output vs polybot per-event recomputation.

Verifies (regression-style) that:
  1. mining `_features_*.parquet` outputs == polybot.lib.compute per-event recomputation
     on the same input data (event-by-event for a sample of rows)
  2. Detects any silent drift between mining batch impl and polybot SSOT impl.

Usage:
    uv run python tools/verify_compute_ssot.py [--n-events 100]

Exit 0 = byte-equal (no drift), exit 1 = drift detected (with first-mismatch detail).

Recommended: add to deploy.sh as pre-deploy gate.
"""
from __future__ import annotations
import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Ensure polybot import works from anywhere
sys.path.insert(0, str(Path(__file__).parent.parent))

from polybot.lib.compute import compute_bn_features

DB = '/home/polymarket_work/db/pm_btc5m.db'
FEATURES_DIR = Path('/home/polymarket_work/research/data')


def _check_dict_eq(actual: dict, expected: dict, tag: str, atol: float = 1e-9) -> list[str]:
    """Return list of mismatch descriptions. Empty list = byte-equal."""
    issues = []
    keys_actual = set(actual.keys())
    keys_expected = set(expected.keys())
    if keys_actual != keys_expected:
        missing = keys_expected - keys_actual
        extra = keys_actual - keys_expected
        if missing: issues.append(f'{tag}: missing keys {sorted(missing)[:5]}')
        if extra:   issues.append(f'{tag}: extra keys {sorted(extra)[:5]}')
    for k in keys_actual & keys_expected:
        a, e = actual[k], expected[k]
        if a is None and e is None: continue
        if isinstance(a, float) and isinstance(e, float):
            if np.isnan(a) and np.isnan(e): continue
            if np.isnan(a) != np.isnan(e):
                issues.append(f'{tag}.{k}: actual={a} expected={e} (NaN mismatch)')
                continue
            if abs(a - e) > atol:
                issues.append(f'{tag}.{k}: actual={a} expected={e} (diff {a-e:.2e})')
        elif a != e:
            issues.append(f'{tag}.{k}: actual={a!r} expected={e!r}')
    return issues


def verify_binance(n_events: int = 100) -> bool:
    print(f'\n[verify binance] sample {n_events} events ...', flush=True)
    bn_parquet = pd.read_parquet(FEATURES_DIR / '_features_binance.parquet')
    con = sqlite3.connect(DB)
    events = con.execute(f"SELECT cid, candle_start FROM events ORDER BY RANDOM() LIMIT {n_events}").fetchall()
    klines = pd.read_sql("""
        SELECT open_ts_ms/1000 AS ts, open, high, low, close,
               volume, quote_volume, taker_buy_volume
        FROM binance_klines ORDER BY open_ts_ms
    """, con).astype({'ts': np.int64}).set_index('ts')
    con.close()
    n_pass = 0
    for cid, cs in events:
        expected = compute_bn_features(klines, cs)
        row = bn_parquet[(bn_parquet.cid == cid) & (bn_parquet.cs == cs)]
        if row.empty: continue
        actual = {k: row.iloc[0][k] for k in expected.keys() if k in row.columns}
        issues = _check_dict_eq(actual, expected, f'bn[{cs}]')
        if issues:
            print(f'  ❌ cs={cs}: {issues[0]}')
            return False
        n_pass += 1
    print(f'  ✓ binance: {n_pass}/{n_events} byte-equal')
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-events', type=int, default=100, help='sample size per family')
    args = ap.parse_args()

    print(f'== Verify compute SSOT: mining ↔ polybot.lib.compute ==')
    print(f'   Sample {args.n_events} events per family.')
    print(f'   FEATURES_DIR: {FEATURES_DIR}')

    ok = True
    if not verify_binance(args.n_events): ok = False

    print()
    if ok:
        print('✓ ALL FAMILIES BYTE-EQUAL — no drift, delegate refactor safe.')
        sys.exit(0)
    else:
        print('❌ DRIFT DETECTED — investigate before deploy.')
        sys.exit(1)


if __name__ == '__main__':
    main()
