"""Paper-time feature materialization for trigger evaluation.

Thin wrapper over `compute.py` (SSOT pure math). For each trigger eval:
  1. Calls compute_pmtrades_features (EP, trades-based) + compute_bn_features
     (BN, when expr touches bn_) → full base set.
  2. For expr cols that are transforms (`__zs24h` / `__zs7d` / `__rank24h`), queries
     feature_history (polybot.db rolling buffer) for past values and calls
     compute_zs / compute_rank.
  3. Writes base feature values to feature_history (for future transforms' history).
  4. Returns 1-row DataFrame for evaluate() to consume.

Public API:
    compute_row(expr, cs, *, trades, up_token, dn_token,
                engine=None, klines=None) -> pd.DataFrame
        trades: list of (ts, side, price, size, asset, proxy_wallet) for cid
                (INTRA features source; b3295eb migration: trades-based not mid-price)
        up_token/dn_token: needed by compute_pmtrades_features for direction split
        engine: SQLAlchemy engine for polybot.db. Required if expr uses transforms.
        klines: pandas DataFrame for BN features (cs-3600 to cs Binance klines).

Coverage:
    EP (p_intra_X + staleness): compute_pmtrades_features (trades-based)
    BN (bn_taker, bn_vol_zscore, ...): compute_bn_features (klines-based)
    Transforms (__zs24h/__zs7d/__rank24h) over any base feature.
"""
from __future__ import annotations
import pandas as pd
from sqlmodel import Session, select

from polybot.lib.compute import (compute_pmtrades_features, compute_bn_features,
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


def compute_row(expr: str, cs: int, *,
                trades=None, up_token=None, dn_token=None,
                engine=None, klines=None) -> pd.DataFrame:
    """1-row DataFrame with columns = base_cols referenced by expr.

    trades:    list of (ts, side, price, size, asset, proxy_wallet) for cid.
               EP features (p_intra_X + staleness) require this.
               Empty list → EP features = NaN (pmt_nan_record fallback).
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

    # INTRA features (b3295eb migration): trades-based.
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

    # If expr atoms touch bn_ (directly or as transform base), compute that family
    # now and merge. Otherwise skip — trades-only path.
    all_atom_bases = set(plain_cols) | bases_to_record
    need_bn = any(b.startswith('bn_') for b in all_atom_bases)

    bn = {}
    if need_bn:
        if klines is None or klines.empty:
            raise NotImplementedError(
                f"expr references bn_ but klines DataFrame not provided "
                f"(scanner must fetch Binance klines for active strategies needing them).")
        bn = compute_bn_features(klines, cs)

    all_features = {**pmt, **bn}

    # Validate all bases exist in computed family set
    for c in plain_cols:
        if c not in all_features:
            raise NotImplementedError(
                f"feature {c!r} not found in intra/bn families. "
                f"Possibly: missing transforms suffix? Unknown feature?")
    for _, base, _ in transform_cols:
        if base not in all_features:
            raise NotImplementedError(
                f"transform base {base!r} not found in intra/bn families.")

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
    UP, DN = 'TOK_UP', 'TOK_DN'
    trades = [                                       # INTRA features source (trades-based)
        (cs - 30, 'BUY', 0.49, 10.0, UP, 'wa'),
        (cs,      'BUY', 0.50, 5.0,  UP, 'wb'),
        (cs + 60, 'BUY', 0.51, 8.0,  UP, 'wa'),
        (cs + 90, 'BUY', 0.52, 3.0,  UP, 'wc'),
    ]
    kw = dict(trades=trades, up_token=UP, dn_token=DN)

    # 1) INTRA plain base computes a finite value
    df = compute_row('p_intra_90_up<0.6', cs, **kw)
    assert df.iloc[0]['p_intra_90_up'] == 0.52
    assert evaluate('p_intra_90_up<0.6', df).tolist() == [True]

    # 2) atoms with no backing family → NotImplementedError
    for unknown in ('p_pre_60_up>0', 'asym_3600_dn<0', 'slope_pre_300_up>0', 'basis_pre_60_up>0'):
        try:
            compute_row(unknown, cs, **kw)
            raise AssertionError(f"should have raised: {unknown!r}")
        except NotImplementedError:
            pass

    # 3) Transform without engine → raise; with engine cold-start → NaN → predicate False
    try:
        compute_row('p_intra_120_up__rank24h>0.5', cs, **kw)
        raise AssertionError("should have raised (no engine)")
    except NotImplementedError:
        pass
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmpdb:
        engine = create_engine(f"sqlite:///{tmpdb.name}")
        SQLModel.metadata.create_all(engine)
        df = compute_row('p_intra_120_up__rank24h>0.5', cs, engine=engine, **kw)
        v = df.iloc[0]['p_intra_120_up__rank24h']
        assert v != v, f"cold-start rank should be NaN, got {v}"   # NaN
        assert evaluate('p_intra_120_up__rank24h>0.5', df).tolist() == [False]

    # 4) Old no-suffix names rejected by validate
    for old_name in ('p_intra_90<0.6', 'max_intra_120<0.4'):
        try:
            compute_row(old_name, cs, **kw)
            raise AssertionError(f"should have raised: {old_name!r}")
        except (NotImplementedError, ValueError):
            pass

    # 5) needs_klines helper + bn_ path (no klines → raise; with klines → value)
    assert needs_klines('bn_taker_buy_ratio_pre_60>0.5') is True
    assert needs_klines('bn_vol_zscore_pre_60__zs24h>0') is True    # transform on bn base
    assert needs_klines('p_intra_90_up<0.6') is False
    assert needs_klines('p_intra_120_dn__rank24h<0.5') is False
    try:
        compute_row('bn_taker_buy_ratio_pre_60>0.5', cs, **kw)
        raise AssertionError("should have raised (no klines)")
    except NotImplementedError:
        pass
    ts_grid = list(range(cs - 3600, cs, 60))
    fake_klines = pd.DataFrame({
        'open':  [0.5] * len(ts_grid),  'high': [0.6] * len(ts_grid), 'low': [0.4] * len(ts_grid),
        'close': [0.55] * len(ts_grid), 'volume': [100.0] * len(ts_grid),
        'quote_volume': [55.0] * len(ts_grid), 'taker_buy_volume': [70.0] * len(ts_grid),
    }, index=pd.Index(ts_grid, name='ts'))
    df = compute_row('bn_taker_buy_ratio_pre_60>0.5', cs, klines=fake_klines, **kw)
    assert df.iloc[0]['bn_taker_buy_ratio_pre_60'] == 0.7  # 70/100

    print("features: OK (intra + transforms + bn via klines)")
