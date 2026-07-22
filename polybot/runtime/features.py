"""Paper-time feature materialization for trigger evaluation.

Thin wrapper over `compute.py` (SSOT pure math). For each trigger eval:
  1. Calls compute_bn_features (BN, when expr touches bn_) → base set.
  2. For expr cols that are transforms (`__zs24h` / `__zs7d` / `__rank24h`), queries
     feature_history (polybot.db rolling buffer) for past values and calls
     compute_zs / compute_rank.
  3. Writes base feature values to feature_history (for future transforms' history).
  4. Returns 1-row DataFrame for evaluate() to consume.

Public API:
    compute_row(expr, cs, *, engine=None, klines=None) -> pd.DataFrame
        engine: SQLAlchemy engine for polybot.db. Required if expr uses transforms.
        klines: pandas DataFrame for BN features (cs-3600 to cs Binance klines).

Coverage:
    BN (bn_taker, bn_vol_zscore, ...): compute_bn_features (klines-based)
    Transforms (__zs24h/__zs7d/__rank24h) over any base feature.
"""
from __future__ import annotations
import pandas as pd
from sqlmodel import Session, select

from polybot.lib.compute.binance import compute_bn_features
from polybot.lib.compute.transforms import compute_zs, compute_rank, parse_transform_col
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
                engine=None, klines=None) -> pd.DataFrame:
    """1-row DataFrame with columns = base_cols referenced by expr.

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

    # If expr atoms touch bn_ (directly or as transform base), compute that family.
    all_atom_bases = set(plain_cols) | bases_to_record
    need_bn = any(b.startswith('bn_') for b in all_atom_bases)

    bn = {}
    if need_bn:
        if klines is None or klines.empty:
            raise NotImplementedError(
                f"expr references bn_ but klines DataFrame not provided "
                f"(scanner must fetch Binance klines for active strategies needing them).")
        bn = compute_bn_features(klines, cs)

    all_features = dict(bn)

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

