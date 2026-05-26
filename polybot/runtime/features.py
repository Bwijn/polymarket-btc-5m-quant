"""Paper-time feature materialization for trigger evaluation.

Thin wrapper over `compute.py` (SSOT pure math). For each trigger eval:
  1. Calls compute_pm_features (PRE features) + compute_pmtrades_features (INTRA features)
     + compute_bn_features (BN features, when expr touches bn_/basis_) → full base set.
  2. For expr cols that are transforms (`__zs24h` / `__zs7d` / `__rank24h`), queries
     feature_history (polybot.db rolling buffer) for past values and calls
     compute_zs / compute_rank.
  3. Writes base feature values to feature_history (for future transforms' history).
  4. Returns 1-row DataFrame for evaluate() to consume.

Public API:
    compute_row(expr, ticks_up, ticks_dn, cs, *, trades, up_token, dn_token,
                engine=None, klines=None) -> pd.DataFrame
        ticks_up/ticks_dn: PM mid-price ticks (PRE features source)
        trades: list of (ts, side, price, size, asset, proxy_wallet) for cid
                (INTRA features source; b3295eb migration: trades-based not mid-price)
        up_token/dn_token: needed by compute_pmtrades_features for direction split
        engine: SQLAlchemy engine for polybot.db. Required if expr uses transforms.
        klines: pandas DataFrame for BN features (cs-3600 to cs Binance klines).

Coverage:
    PRE (slope_pre, mean_pre, asym, vol_ratio, ...): compute_pm_features (ticks-based)
    INTRA (delta_intra, max/min/mean_intra, pmt_whale, pmt_flow_imb, ...): compute_pmtrades_features (trades-based)
    BN (bn_taker, bn_vol_zscore, ...): compute_bn_features (klines-based)
    basis (basis_pre_X): compute_basis_features (PM + BN derived)
    Transforms (__zs24h/__zs7d/__rank24h) over any base feature.
"""
from __future__ import annotations
import pandas as pd
from sqlmodel import Session, select

from polybot.lib.compute import (compute_pm_features, compute_pmtrades_features,
                         compute_bn_features, compute_basis_features,
                         compute_zs, compute_rank, parse_transform_col)
from polybot.lib.expr_eval_v1 import validate

# Resolve FeatureHistory across two import contexts:
#   scanner runtime  (main.py inserts polybot/ as sys.path[0]) → 'from runtime.models import X'
#   pytest / -m run  (project root in sys.path)                → 'from polybot.runtime.models import X'
try:
    from polybot.runtime.models import FeatureHistory
except ImportError:
    from runtime.models import FeatureHistory  # type: ignore


def needs_klines(expr: str) -> bool:
    """True if expr references bn_* or basis_* (directly or via transform base)."""
    ast = validate(expr)
    cols = set()
    for p in ast['predicates']:
        cols.add(p['lhs']['col'])
        if p['rhs']['kind'] == 'atom':
            cols.add(p['rhs']['col'])
    # Resolve transform cols to their base, then check prefix
    all_bases = set()
    for c in cols:
        info = parse_transform_col(c)
        all_bases.add(info[0] if info else c)
    return any(b.startswith('bn_') or b.startswith('basis_') for b in all_bases)


def compute_row(expr: str, ticks_up, ticks_dn, cs: int, *,
                trades=None, up_token=None, dn_token=None,
                engine=None, klines=None) -> pd.DataFrame:
    """1-row DataFrame with columns = base_cols referenced by expr.

    ticks_up:  PM /prices-history ticks (PRE features source)
    ticks_dn:  PM /prices-history ticks (PRE features source)
    trades:    list of (ts, side, price, size, asset, proxy_wallet) for cid.
               INTRA features (max/min/mean_intra, delta_intra, pmt_*) require this.
               Empty list → INTRA features = NaN (pmt_nan_record fallback).
    up_token/dn_token: required if trades passed (compute_pmtrades_features needs).
    cs:        candle_start (unix seconds)
    engine:    SQLAlchemy engine for polybot.db. Required for transform atoms.
    klines:    pandas DataFrame for BN features ([cs-3600, cs] coverage).
    """
    ast = validate(expr)
    needed_cols = set()
    for p in ast['predicates']:
        needed_cols.add(p['lhs']['col'])
        if p['rhs']['kind'] == 'atom':
            needed_cols.add(p['rhs']['col'])
        if p['lhs']['transforms']:
            # Legacy expr_eval transforms list — paper-time uses suffix parsing instead
            # since materialized transform cols (like `X__zs24h` in features.parquet)
            # come through as atom['col'] = full name with transforms=[].
            raise NotImplementedError(
                f"expr_eval atom transforms not supported; use materialized suffix instead.")

    pm  = compute_pm_features(ticks_up, ticks_dn, cs)
    # INTRA features (b3295eb migration): trades-based, not mid-price ticks.
    pmt = compute_pmtrades_features(trades or [], cs, up_token, dn_token) \
          if (up_token and dn_token) else {}

    # Decompose cols: plain base vs transform
    plain_cols = []
    transform_cols = []   # list of (full_col, base, spec)
    bases_to_record = set()
    for c in needed_cols:
        info = parse_transform_col(c)
        if info is None:
            plain_cols.append(c)
        else:
            base, spec = info
            transform_cols.append((c, base, spec))
            bases_to_record.add(base)

    # If expr atoms touch bn_/basis_ (directly or as transform base), compute those
    # families now and merge into the feature lookup. Otherwise skip — PM-only path.
    all_atom_bases = set(plain_cols) | bases_to_record
    need_bn    = any(b.startswith('bn_')    for b in all_atom_bases)
    need_basis = any(b.startswith('basis_') for b in all_atom_bases)

    bn = {}
    basis_d = {}
    if need_bn or need_basis:
        if klines is None or klines.empty:
            raise NotImplementedError(
                f"expr references bn_/basis but klines DataFrame not provided "
                f"(scanner must fetch Binance klines for active strategies needing them).")
        bn = compute_bn_features(klines, cs)
        if need_basis:
            basis_d = compute_basis_features(pm, bn)

    all_features = {**pm, **pmt, **bn, **basis_d}

    # Validate all bases exist in computed family set
    for c in plain_cols:
        if c not in all_features:
            raise NotImplementedError(
                f"feature {c!r} not found in pm/bn/basis families. "
                f"Possibly: missing transforms suffix? Unknown feature?")
    for _, base, _ in transform_cols:
        if base not in all_features:
            raise NotImplementedError(
                f"transform base {base!r} not found in pm/bn/basis families.")

    row = {}
    for c in plain_cols:
        row[c] = all_features[c]

    if transform_cols:
        if engine is None:
            offenders = [c for c, _, _ in transform_cols]
            raise NotImplementedError(
                f"transforms {offenders} need db engine for history query — "
                f"caller must pass engine= when expr uses __zs24h/__zs7d/__rank24h.")
        with Session(engine) as session:
            # Query past values once per distinct base (multiple transforms may share base)
            base_past = {}
            for _, base, spec in transform_cols:
                if base in base_past:
                    continue
                w = spec['window']
                past = session.exec(
                    select(FeatureHistory.value)
                    .where(FeatureHistory.feature_name == base)
                    .where(FeatureHistory.cs < cs)
                    .order_by(FeatureHistory.cs.desc())
                    .limit(w)
                ).all()
                base_past[base] = past

            for full_col, base, spec in transform_cols:
                current = all_features[base]
                past = base_past[base]
                op = spec['op']
                mp = spec['min_periods']
                if op == 'zs':
                    row[full_col] = compute_zs(current, past, mp)
                elif op == 'rank':
                    row[full_col] = compute_rank(current, past, mp)
                else:
                    raise ValueError(f"unknown transform op {op!r}")

    # Persist current cs's base values for future transforms (upsert on PK)
    if engine is not None and bases_to_record:
        with Session(engine) as session:
            for base in bases_to_record:
                val = all_features[base]
                # SQLite null for NaN to keep numeric ops clean downstream
                val_db = None if (val != val) else float(val)  # NaN check via self-ne
                existing = session.get(FeatureHistory, (base, cs))
                if existing:
                    existing.value = val_db
                else:
                    session.add(FeatureHistory(feature_name=base, cs=cs, value=val_db))
            session.commit()

    return pd.DataFrame([row])


# ---- self-test --------------------------------------------------------------
if __name__ == '__main__':
    import tempfile
    from sqlmodel import SQLModel, create_engine
    # FeatureHistory already imported at module top (try/except)
    from polybot.lib.expr_eval_v1 import evaluate

    cs = 1771027200  # 2026-02-14 00:00 UTC Saturday
    ticks_up = [
        {'t': cs,       'p': 0.50},
        {'t': cs + 60,  'p': 0.40},
        {'t': cs + 120, 'p': 0.45},
        {'t': cs + 180, 'p': 0.42},
        {'t': cs + 240, 'p': 0.38},
    ]
    ticks_dn = [
        {'t': cs,       'p': 0.50},
        {'t': cs + 60,  'p': 0.60},
        {'t': cs + 120, 'p': 0.55},
        {'t': cs + 180, 'p': 0.58},
        {'t': cs + 240, 'p': 0.62},
    ]

    # Plain base features (no engine needed) — unchanged from Task 1
    df = compute_row('p_intra_60_up<0.445 & is_weekend==1', ticks_up, ticks_dn, cs)
    assert df.iloc[0]['p_intra_60_up'] == 0.40
    assert df.iloc[0]['is_weekend'] == 1
    assert evaluate('p_intra_60_up<0.445 & is_weekend==1', df).tolist() == [True]

    df = compute_row('max_intra_120_up<0.6', ticks_up, ticks_dn, cs)
    assert df.iloc[0]['max_intra_120_up'] == 0.50

    # Transforms without engine → raise
    try:
        compute_row('delta_intra_60_up__rank24h>0.5', ticks_up, ticks_dn, cs)
        raise AssertionError("should have raised (no engine)")
    except NotImplementedError:
        pass

    # Transforms with engine: cold start → NaN, predicate False
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmpdb:
        engine = create_engine(f"sqlite:///{tmpdb.name}")
        SQLModel.metadata.create_all(engine)

        # First trigger: empty feature_history → transforms NaN → predicate False
        df = compute_row('delta_intra_60_up__rank24h>0.5', ticks_up, ticks_dn, cs, engine=engine)
        # delta_intra_60_up = 0.40 - 0.50 = -0.10; with 0 past values, rank NaN
        assert df.iloc[0]['delta_intra_60_up__rank24h'] != df.iloc[0]['delta_intra_60_up__rank24h']  # NaN
        assert evaluate('delta_intra_60_up__rank24h>0.5', df).tolist() == [False]

        # Manually pre-seed history (simulating warm-start or accumulated paper data)
        # FeatureHistory already imported at module top (try/except)
        with Session(engine) as session:
            for i in range(60):  # 60 ≈ min_periods=50 ✓
                session.add(FeatureHistory(
                    feature_name='delta_intra_60_up',
                    cs=cs - 300 * (60 - i),  # 60 past 5min candles
                    value=-0.20 + i * 0.005,  # values from -0.20 to +0.095
                ))
            session.commit()

        # Now with seeded history, rank should compute
        df = compute_row('delta_intra_60_up__rank24h>0.5', ticks_up, ticks_dn, cs, engine=engine)
        rk = df.iloc[0]['delta_intra_60_up__rank24h']
        assert 0.0 <= rk <= 1.0, rk
        # current = -0.10; past = [-0.20, -0.195, ..., +0.095] (60 vals)
        # Position of -0.10 among sorted [-0.20..+0.095]+[-0.10]: approx 20-22 percentile
        assert rk < 0.5, f"expected current=-0.10 below median, got {rk}"

        # Engine also writes base value for current cs (idempotent upsert)
        with Session(engine) as session:
            row = session.get(FeatureHistory, ('delta_intra_60_up', cs))
            assert row is not None
            assert abs(row.value - (-0.10)) < 1e-9

    # Old no-suffix names rejected by validate
    for old_name in ('p_intra_60<0.445', 'max_intra_120<0.4'):
        try:
            compute_row(old_name, ticks_up, ticks_dn, cs)
            raise AssertionError(f"should have raised: {old_name!r}")
        except (NotImplementedError, ValueError):
            pass

    # bn_ / basis_ 无 klines → raise; 有 klines → 算
    for bn_expr in ('bn_chg_pct_pre_300>0', 'basis_pre_60_up>0'):
        try:
            compute_row(bn_expr, ticks_up, ticks_dn, cs)
            raise AssertionError(f"should have raised (no klines): {bn_expr!r}")
        except NotImplementedError:
            pass

    # needs_klines helper
    assert needs_klines('bn_chg_pct_pre_300>0') is True
    assert needs_klines('basis_pre_60_up>0') is True
    assert needs_klines('bn_vol_zscore_pre_60__zs24h>0') is True   # transform on bn base
    assert needs_klines('basis_pre_60_up__zs7d<0') is True
    assert needs_klines('p_intra_60_up<0.445 & is_weekend==1') is False
    assert needs_klines('delta_intra_60_dn__rank24h<0.5') is False

    # With synthetic klines, bn_ atom returns a value (not raise)
    import pandas as pd
    import numpy as np
    ts_grid = list(range(cs - 3600, cs, 60))
    fake_klines = pd.DataFrame({
        'open':  [0.5] * len(ts_grid),
        'high':  [0.6] * len(ts_grid),
        'low':   [0.4] * len(ts_grid),
        'close': [0.55] * len(ts_grid),
        'volume':           [100.0] * len(ts_grid),
        'quote_volume':     [55.0] * len(ts_grid),
        'taker_buy_volume': [70.0] * len(ts_grid),
    }, index=pd.Index(ts_grid, name='ts'))
    df = compute_row('bn_taker_buy_ratio_pre_60>0.5', ticks_up, ticks_dn, cs, klines=fake_klines)
    assert df.iloc[0]['bn_taker_buy_ratio_pre_60'] == 0.7  # 70/100

    print("features: OK (transforms + bn_/basis_ via klines + history idempotent)")
