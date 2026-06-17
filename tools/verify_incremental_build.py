"""Differential test: incremental base build == full rebuild (byte/value-exact).

Context: incremental build (pm/binance/pmtrades) reuses existing parquet rows and
  computes only new events. SAFE only if base features are per-event with no
  cross-event dependency (an old row never changes when new events arrive). This
  test PROVES it: truncate the full parquet by last K events, run the builder in
  incremental mode (recomputes the K), assert the result equals the original full
  rebuild value-for-value. Any mismatch = drift = abort (0 tolerance, real money).
Source: current _features_{pm,binance,pmtrades}.parquet (= full-rebuild ground truth).
Expected: ✓ all builders incremental == full; exit 1 on any drift.

Safety: backs up each parquet to .bak before truncating; restores in finally even
  on crash. If killed mid-run, restore manually: mv <f>.bak <f>.
"""
from __future__ import annotations
import sys
import pandas as pd

sys.path.insert(0, '/home/polymarket_work')
sys.path.insert(0, '/home/polymarket_work/scratch/research')
from features import pm, binance, pmtrades            # noqa: E402
from features._common import OUT_DIR                   # noqa: E402

K = 200   # recompute last-K events incrementally and compare to full

CASES = [
    ('pm',       pm.build,       OUT_DIR / '_features_pm.parquet'),
    ('binance',  binance.build,  OUT_DIR / '_features_binance.parquet'),
    ('pmtrades', pmtrades.build, OUT_DIR / '_features_pmtrades.parquet'),
]


def check(name, build_fn, path) -> bool:
    full = pd.read_parquet(path).sort_values('cs').reset_index(drop=True)
    bak = path.with_suffix('.parquet.bak')
    full.to_parquet(bak, index=False, compression='zstd')      # ground-truth backup
    try:
        truncated = full.iloc[:-K].copy()                       # drop last K events
        truncated.to_parquet(path, index=False, compression='zstd')
        build_fn(incremental=True)                              # recompute the K, append
        got = pd.read_parquet(path).sort_values('cs').reset_index(drop=True)
        # align columns + compare value-for-value (dtype coercion from concat ok)
        if list(got.columns) != list(full.columns):
            print(f"  ✗ {name}: column set/order differs")
            return False
        try:
            pd.testing.assert_frame_equal(got, full, check_dtype=False, check_exact=True)
        except AssertionError as e:
            print(f"  ✗ {name}: VALUE DRIFT incremental vs full:\n{str(e)[:600]}")
            return False
        print(f"  ✓ {name}: incremental(last {K}) == full, {got.shape[0]} rows value-exact")
        return True
    finally:
        full.to_parquet(path, index=False, compression='zstd')  # restore original
        bak.unlink(missing_ok=True)


def main():
    print(f"== verify incremental build == full (recompute last {K} events) ==")
    ok = all(check(n, b, p) for n, b, p in CASES)
    if ok:
        print("✓ ALL incremental builds value-exact vs full — append-reuse safe.")
    else:
        print("✗ DRIFT detected — incremental build NOT safe, do not use.")
        sys.exit(1)


if __name__ == '__main__':
    main()
