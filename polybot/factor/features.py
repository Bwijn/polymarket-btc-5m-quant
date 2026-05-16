"""Paper-time feature materialization for trigger evaluation.

Thin wrapper over `compute.py` (SSOT pure math). For each trigger eval:
  1. Calls compute_pm_features once → all PM base values.
  2. For expr cols that are transforms (`__zs24h` / `__zs7d` / `__rank24h`), queries
     feature_history (polybot.db rolling buffer) for past values and calls
     compute_zs / compute_rank.
  3. Writes base feature values to feature_history (for future transforms' history).
  4. Returns 1-row DataFrame for evaluate() to consume.

Public API:
    compute_row(expr, ticks_up, ticks_dn, cs, engine=None) -> pd.DataFrame
        engine: SQLAlchemy engine for polybot.db. Required if expr uses transforms.
                Without engine, transform atoms raise NotImplementedError.

Coverage:
    PM family (intra+pre+derived+time): full set from compute_pm_features
    Transforms (__zs24h/__zs7d/__rank24h) over any PM base feature
    NotImplementedError for: bn_* / basis_* (need scanner Binance fetch wiring)
"""
from __future__ import annotations
import pandas as pd
from sqlmodel import Session, select

from .compute import compute_pm_features, compute_zs, compute_rank, parse_transform_col
from .expr_eval_v1 import validate

# Resolve FeatureHistory across two import contexts:
#   scanner runtime  (main.py inserts polybot/ as sys.path[0]) → 'from models import X'
#   self-test / -m   (project root in sys.path)                → 'from polybot.models import X'
try:
    from polybot.models import FeatureHistory
except ImportError:
    from models import FeatureHistory  # type: ignore


def compute_row(expr: str, ticks_up, ticks_dn, cs: int, engine=None) -> pd.DataFrame:
    """1-row DataFrame with columns = base_cols referenced by expr.

    ticks_up: list of {t, p} from PM /prices-history(up_token)
    ticks_dn: list of {t, p} from PM /prices-history(down_token)
    cs:       candle_start (unix seconds)
    engine:   SQLAlchemy engine for polybot.db. Required for transform atoms.
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

    pm = compute_pm_features(ticks_up, ticks_dn, cs)

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

    # Validate all bases exist in PM family
    for c in plain_cols:
        if c not in pm:
            raise NotImplementedError(
                f"feature {c!r} not in PM family: needs Binance/basis wiring (scanner "
                f"doesn't fetch klines yet) — next infra task.")
    for _, base, _ in transform_cols:
        if base not in pm:
            raise NotImplementedError(
                f"transform base {base!r} not in PM family: needs Binance/basis wiring.")

    row = {}
    for c in plain_cols:
        row[c] = pm[c]

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
                current = pm[base]
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
                val = pm[base]
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
    from .expr_eval_v1 import evaluate

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
        from polybot.models import FeatureHistory
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

    # Bn / basis 还是 raise (这个 task 不解决)
    for bad in ('bn_chg_pct_pre_300>0', 'basis_pre_60_up>0'):
        try:
            compute_row(bad, ticks_up, ticks_dn, cs)
            raise AssertionError(f"should have raised: {bad!r}")
        except NotImplementedError:
            pass

    print("features: OK (transforms via feature_history + history writes idempotent)")
