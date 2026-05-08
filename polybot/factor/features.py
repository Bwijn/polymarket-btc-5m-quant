"""Compute single-candle features from intra-bar UP-token ticks + cs.

SSOT for paper-time + (future) live-time feature materialization. Mirrors
mining-time features_v13.parquet semantics.

Public API:
    compute_row(expr, ticks, cs) -> pd.DataFrame  # 1-row, columns referenced by expr

Coverage (paper MVP):
    intra:  p_intra_X, max/mean/min/std/rng_intra_X, delta_intra_X (any X)
    static: p_open
    time:   is_weekend, is_friday, is_saturday, is_sunday, dow, hour_utc, minute_of_hour
    NotImplementedError for: hour_bucket, we_hour, p_pre_*, *_pre_*, z_pre_*,
                              chg_rate_*, vol_ratio_*, slope_diff_*, asym_*, dist_fair_open

Non-MVP base_cols raise NotImplementedError loudly — never silently 0/NaN.
"""
from __future__ import annotations
from datetime import datetime, timezone
import pandas as pd
from .entry_price import get_price_at
from .expr_eval_v1 import validate

_INTRA_STATS = ('max', 'mean', 'min', 'std', 'rng')


def compute_row(expr: str, ticks, cs: int) -> pd.DataFrame:
    """1-row DataFrame with columns = base_cols referenced by expr."""
    ast = validate(expr)
    cols = set()
    for p in ast['predicates']:
        cols.add(p['lhs']['col'])
        if p['rhs']['kind'] == 'atom':
            cols.add(p['rhs']['col'])
        if p['lhs']['transforms']:
            raise NotImplementedError(
                f"transforms on {p['lhs']['col']!r} not supported in paper MVP")
    return pd.DataFrame([{c: _compute(c, ticks, cs) for c in cols}])


def _compute(col: str, ticks, cs: int):
    if col == 'p_open':
        return get_price_at(ticks, 0, cs)

    if col == 'is_weekend':  return 1 if _dow(cs) in (5, 6) else 0
    if col == 'is_friday':   return 1 if _dow(cs) == 4 else 0
    if col == 'is_saturday': return 1 if _dow(cs) == 5 else 0
    if col == 'is_sunday':   return 1 if _dow(cs) == 6 else 0
    if col == 'dow':              return _dow(cs)
    if col == 'hour_utc':         return _utc(cs).hour
    if col == 'minute_of_hour':   return _utc(cs).minute

    if col.startswith('p_intra_'):
        return get_price_at(ticks, int(col[len('p_intra_'):]), cs)

    if col.startswith('delta_intra_'):
        x = int(col[len('delta_intra_'):])
        p_x, p_o = get_price_at(ticks, x, cs), get_price_at(ticks, 0, cs)
        return None if p_x is None or p_o is None else p_x - p_o

    for stat in _INTRA_STATS:
        prefix = f'{stat}_intra_'
        if col.startswith(prefix):
            return _intra_stat(stat, ticks, cs, int(col[len(prefix):]))

    raise NotImplementedError(
        f"feature {col!r} not supported in paper MVP. "
        f"Add to factor/features.py if a new hypothesis needs it.")


def _intra_stat(stat: str, ticks, cs: int, x: int):
    """Stat over UP-token prices in [cs, cs+x]. Carry-forward not applied —
    we use only the raw ticks observed in window (matches mining: no synthetic
    fill of missing seconds inside the window).
    """
    if ticks is None or not ticks:
        return None
    vals = [t['p'] for t in ticks if cs <= t['t'] <= cs + x]
    if not vals:
        return None
    if stat == 'max':  return max(vals)
    if stat == 'min':  return min(vals)
    if stat == 'mean': return sum(vals) / len(vals)
    if stat == 'rng':  return max(vals) - min(vals)
    if stat == 'std':
        m = sum(vals) / len(vals)
        return (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5
    raise ValueError(f"unknown stat {stat!r}")


def _utc(cs: int) -> datetime:
    return datetime.fromtimestamp(cs, tz=timezone.utc)


def _dow(cs: int) -> int:
    return _utc(cs).weekday()


# ---- self-test --------------------------------------------------------------
if __name__ == '__main__':
    from .expr_eval_v1 import evaluate

    cs = 1771027200  # 2026-02-14 00:00 UTC, Saturday → is_weekend=1, dow=5
    ticks = [
        {'t': 1771027200, 'p': 0.50},   # cs+0  → p_open
        {'t': 1771027260, 'p': 0.40},   # cs+60
        {'t': 1771027320, 'p': 0.45},   # cs+120
        {'t': 1771027380, 'p': 0.42},   # cs+180
        {'t': 1771027440, 'p': 0.38},   # cs+240
    ]

    # H5: p_intra_60<0.445 & is_weekend==1
    df = compute_row('p_intra_60<0.445 & is_weekend==1', ticks, cs)
    assert list(df.columns) == ['p_intra_60', 'is_weekend'] or set(df.columns) == {'p_intra_60', 'is_weekend'}
    assert df.iloc[0]['p_intra_60'] == 0.40
    assert df.iloc[0]['is_weekend'] == 1
    assert evaluate('p_intra_60<0.445 & is_weekend==1', df).tolist() == [True]

    # H6: max_intra_120<0.4
    df = compute_row('max_intra_120<0.4', ticks, cs)
    assert df.iloc[0]['max_intra_120'] == 0.50  # max over [cs, cs+120] = {0.50, 0.40, 0.45}
    assert evaluate('max_intra_120<0.4', df).tolist() == [False]

    # H6 hit case: prices all <0.4
    ticks_low = [{'t': cs, 'p': 0.30}, {'t': cs + 60, 'p': 0.35}, {'t': cs + 120, 'p': 0.32}]
    df = compute_row('max_intra_120<0.4', ticks_low, cs)
    assert df.iloc[0]['max_intra_120'] == 0.35
    assert evaluate('max_intra_120<0.4', df).tolist() == [True]

    # delta_intra
    df = compute_row('delta_intra_60<-0.05', ticks, cs)
    assert abs(df.iloc[0]['delta_intra_60'] - (-0.10)) < 1e-9  # 0.40 - 0.50
    assert evaluate('delta_intra_60<-0.05', df).tolist() == [True]

    # NotImplementedError 路径
    for bad in ('asym_3600<0', 'std_pre_900>0.001', 'we_hour==4', 'hour_bucket==0',
                'chg_rate_pre_3600>0.05', 'p_intra_60__zs24h>2'):
        try:
            compute_row(bad, ticks, cs)
            raise AssertionError(f"should have raised: {bad!r}")
        except NotImplementedError:
            pass

    # weekday weekend logic at boundaries
    # 2026-02-13 Friday → is_friday=1, is_weekend=0
    fri = 1770940800
    df = compute_row('is_friday==1 & is_weekend==0', ticks, fri)
    assert evaluate('is_friday==1 & is_weekend==0', df).tolist() == [True]

    print("features: OK (H5 + H6 + delta + boundaries + NotImpl gates)")
