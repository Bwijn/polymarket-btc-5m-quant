"""Orchestrate feature builders → features.parquet (mining input SSOT).

Pipeline graph:
  binance   ─┐
  pmtrades  ─┼─→ transforms (all base) → merge → features.parquet
  futures   ─┘

Per-source upsert: --source <name> rebuilds only that sub-parquet.
Full rebuild: --source all.

Source → output mapping:
  binance   binance_klines                         → _features_binance.parquet  (~14 cols)
  pmtrades  trades                                  → _features_pmtrades.parquet (~11+ cols)
  futures   binance_funding_rate/oi_hist/ls_*       → _features_futures.parquet  (~7 cols)
  transforms all base                               → _features_transforms.parquet
  merge     all sub-parquets joined on (cid, cs)    → features.parquet

Usage:
  uv run python scratch/research/build_features.py                       # all sources + merge
  uv run python scratch/research/build_features.py --source pmtrades     # rebuild just one
  uv run python scratch/research/build_features.py --source pmtrades --source transforms --source merge
                                                                          # rebuild + cascade
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

# project root on sys.path so `polybot.lib.compute` resolves
# (features/pmtrades.py delegates to polybot.lib.compute for SSOT)
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from features import binance, futures, merge, pmtrades, transforms
from features._common import OUT_DIR

BUILDERS = {
    'binance':    binance.build,
    'pmtrades':   pmtrades.build,
    'futures':    futures.build,
    'transforms': transforms.build,
    'merge':      merge.build,
}
ORDER = list(BUILDERS)   # canonical pipeline order, respects deps


# Builders supporting incremental (per-event, no cross-event dep). Others
# (futures/basis fast; transforms needs full base; merge is I/O) stay full —
# they still cover all events (old+new) so the merge stays consistent.
INCREMENTAL_CAPABLE = {'binance', 'pmtrades'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', action='append',
                    choices=['all'] + list(BUILDERS),
                    help='which builder(s) to run; repeat flag for multiple. default=all')
    ap.add_argument('--incremental', action='store_true',
                    help='only compute events not already in sub-parquet (pm/binance/'
                         'pmtrades). transforms/merge always full (already fast).')
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    sources = args.source or ['all']
    if 'all' in sources:
        targets = ORDER
    else:
        targets = [s for s in ORDER if s in set(sources)]   # preserve canonical order

    for t in targets:
        if args.incremental and t in INCREMENTAL_CAPABLE:
            BUILDERS[t](incremental=True)
        else:
            BUILDERS[t]()


if __name__ == '__main__':
    main()
