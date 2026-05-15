"""Compute single-candle features from intra-bar UP+DN-token ticks + cs.

SSOT for paper-time feature materialization. Mirrors mining-time
features.parquet semantics (direction-split _up/_dn schema).

Public API:
    compute_row(expr, ticks_up, ticks_dn, cs) -> pd.DataFrame
        1-row DataFrame, columns = base_cols referenced by expr

Coverage (paper):
    intra _up/_dn:  p_intra_X_{up,dn}, max/mean/min/std/rng/delta_intra_X_{up,dn}
    static _up/_dn: p_open_{up,dn}
    time (no dir):  is_weekend, is_friday, is_saturday, is_sunday,
                    dow, hour_utc, minute_of_hour
    NotImplementedError for: anything else — transforms (__zs24h/__rank24h/__lag1),
                              pre-event features (*_pre_*), Binance (bn_*),
                              basis_*, futures meta. M1-M5 work to extend.
"""
from __future__ import annotations
from datetime import datetime, timezone
import pandas as pd
from .entry_price import get_price_at
from .expr_eval_v1 import validate

_INTRA_STATS = ('max', 'mean', 'min', 'std', 'rng')


def compute_row(expr: str, ticks_up, ticks_dn, cs: int) -> pd.DataFrame:
    """1-row DataFrame with columns = base_cols referenced by expr.

    ticks_up: list of {t, p} from PM /prices-history(up_token)
    ticks_dn: list of {t, p} from PM /prices-history(down_token)
    """
    ast = validate(expr)
    cols = set()
    for p in ast['predicates']:
        cols.add(p['lhs']['col'])
        if p['rhs']['kind'] == 'atom':
            cols.add(p['rhs']['col'])
        if p['lhs']['transforms']:
            raise NotImplementedError(
                f"transforms on {p['lhs']['col']!r} not supported in paper-time.")
    return pd.DataFrame([{c: _compute(c, ticks_up, ticks_dn, cs) for c in cols}])


def _compute(col: str, ticks_up, ticks_dn, cs: int):
    # Time features (no direction suffix)
    if col == 'is_weekend':     return 1 if _dow(cs) in (5, 6) else 0
    if col == 'is_friday':      return 1 if _dow(cs) == 4 else 0
    if col == 'is_saturday':    return 1 if _dow(cs) == 5 else 0
    if col == 'is_sunday':      return 1 if _dow(cs) == 6 else 0
    if col == 'dow':            return _dow(cs)
    if col == 'hour_utc':       return _utc(cs).hour
    if col == 'minute_of_hour': return _utc(cs).minute

    # Direction-suffixed price features
    if col.endswith('_up'):
        return _compute_price(col[:-3], ticks_up, cs)
    if col.endswith('_dn'):
        return _compute_price(col[:-3], ticks_dn, cs)

    raise NotImplementedError(
        f"feature {col!r} not supported in paper-time. "
        f"Direction-split schema requires '_up' or '_dn' suffix on price features. "
        f"Time features (is_weekend, dow, ...) need no suffix.")


def _compute_price(base: str, ticks, cs: int):
    """base = direction-stripped col name (e.g. 'p_intra_60', 'max_intra_120', 'p_open')."""
    if base == 'p_open':
        return get_price_at(ticks, 0, cs)

    if base.startswith('p_intra_'):
        return get_price_at(ticks, int(base[len('p_intra_'):]), cs)

    if base.startswith('delta_intra_'):
        x = int(base[len('delta_intra_'):])
        p_x, p_o = get_price_at(ticks, x, cs), get_price_at(ticks, 0, cs)
        return None if p_x is None or p_o is None else p_x - p_o

    for stat in _INTRA_STATS:
        prefix = f'{stat}_intra_'
        if base.startswith(prefix):
            return _intra_stat(stat, ticks, cs, int(base[len(prefix):]))

    raise NotImplementedError(
        f"base feature {base!r} not supported in paper-time (M1-M5 to extend).")


def _intra_stat(stat: str, ticks, cs: int, x: int):
    """Stat over prices in [cs, cs+x]. No carry-forward — only raw ticks observed
    in the window (matches mining: no synthetic fill of missing seconds)."""
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
    ticks_up = [
        {'t': 1771027200, 'p': 0.50},   # cs+0
        {'t': 1771027260, 'p': 0.40},   # cs+60
        {'t': 1771027320, 'p': 0.45},   # cs+120
        {'t': 1771027380, 'p': 0.42},   # cs+180
        {'t': 1771027440, 'p': 0.38},   # cs+240
    ]
    ticks_dn = [
        {'t': 1771027200, 'p': 0.50},   # complement (approx)
        {'t': 1771027260, 'p': 0.60},
        {'t': 1771027320, 'p': 0.55},
        {'t': 1771027380, 'p': 0.58},
        {'t': 1771027440, 'p': 0.62},
    ]

    # H5 new schema
    df = compute_row('p_intra_60_up<0.445 & is_weekend==1', ticks_up, ticks_dn, cs)
    assert df.iloc[0]['p_intra_60_up'] == 0.40
    assert df.iloc[0]['is_weekend'] == 1
    assert evaluate('p_intra_60_up<0.445 & is_weekend==1', df).tolist() == [True]

    # _dn variant pulls from ticks_dn
    df = compute_row('p_intra_60_dn>0.555', ticks_up, ticks_dn, cs)
    assert df.iloc[0]['p_intra_60_dn'] == 0.60
    assert evaluate('p_intra_60_dn>0.555', df).tolist() == [True]

    # max_intra_X_up
    df = compute_row('max_intra_120_up<0.6', ticks_up, ticks_dn, cs)
    assert df.iloc[0]['max_intra_120_up'] == 0.50

    # delta_intra_X_up
    df = compute_row('delta_intra_60_up<-0.05', ticks_up, ticks_dn, cs)
    assert abs(df.iloc[0]['delta_intra_60_up'] - (-0.10)) < 1e-9

    # Old no-suffix names rejected by validate() (BASE_COLS 不含, ValueError)
    # 强制新 _up/_dn schema, 不留 fallback
    for old_name in ('p_intra_60<0.445', 'max_intra_120<0.4', 'delta_intra_60<-0.05'):
        try:
            compute_row(old_name, ticks_up, ticks_dn, cs)
            raise AssertionError(f"should have raised: {old_name!r}")
        except (NotImplementedError, ValueError):
            pass

    # Transforms / 未支持 feature 类 (validate 或 compute_row 任一阶段拒绝)
    for bad in ('p_intra_60_up__zs24h>2',
                'bn_chg_pct_pre_300>0',
                'basis_pre_60_up>0',
                'mean_pre_900_up<0.5',
                'we_hour==4',
                'hour_bucket==0'):
        try:
            compute_row(bad, ticks_up, ticks_dn, cs)
            raise AssertionError(f"should have raised: {bad!r}")
        except (NotImplementedError, ValueError):
            pass

    # Friday boundary (weekday)
    fri = 1770940800  # 2026-02-13 Friday
    df = compute_row('is_friday==1 & is_weekend==0', ticks_up, ticks_dn, fri)
    assert evaluate('is_friday==1 & is_weekend==0', df).tolist() == [True]

    print("features: OK (new _up/_dn schema, intra dispatch, old names rejected)")
