"""Paper-time feature materialization for trigger evaluation.

Thin wrapper over `compute.py` (SSOT pure math). For each trigger eval, calls
compute_pm_features once and picks out the cols expr references — same math
as mining (`scratch/factor_lab/build_features.py`), verified byte-identical by
`scratch/factor_lab/test_compute_equivalence.py`.

Public API:
    compute_row(expr, ticks_up, ticks_dn, cs) -> pd.DataFrame
        1-row DataFrame, columns = base_cols referenced by expr.

Currently supported expr atoms (full PM family from compute_pm_features):
    intra _up/_dn:     p_intra_X, max/mean/min/std/rng/delta_intra_X, chg_rate_intra_X
    pre   _up/_dn:     p_pre_X, mean/std/rng/slope_pre_W, delta_pre_X, chg_rate_pre_W,
                       z_pre_W
    derived _up/_dn:   p_open, vol_ratio_900_3600, slope_diff_300_1800, asym_3600,
                       dist_fair_open
    time (no suffix):  is_weekend, is_friday, is_saturday, is_sunday, dow, hour_utc,
                       hour_bucket, minute_of_hour, we_hour

NotImplementedError for:
    - transforms (__zs24h/__zs7d/__rank24h): need stateful rolling buffer in
      polybot.db — next infra task.
    - bn_* / basis_*: scanner doesn't fetch Binance klines yet — wire scanner
      + thread klines through to compute_bn_features / compute_basis_features.
"""
from __future__ import annotations
import pandas as pd

from .compute import compute_pm_features
from .expr_eval_v1 import validate


def compute_row(expr: str, ticks_up, ticks_dn, cs: int) -> pd.DataFrame:
    """1-row DataFrame with columns = base_cols referenced by expr.

    ticks_up: list of {t, p} from PM /prices-history(up_token)
    ticks_dn: list of {t, p} from PM /prices-history(down_token)
    cs:       candle_start (unix seconds)
    """
    ast = validate(expr)
    cols = set()
    for p in ast['predicates']:
        cols.add(p['lhs']['col'])
        if p['rhs']['kind'] == 'atom':
            cols.add(p['rhs']['col'])
        if p['lhs']['transforms']:
            raise NotImplementedError(
                f"transform on {p['lhs']['col']!r}: stateful rolling buffer not yet "
                f"in polybot.db — next infra task.")

    pm = compute_pm_features(ticks_up, ticks_dn, cs)

    row = {}
    for c in cols:
        if c not in pm:
            raise NotImplementedError(
                f"feature {c!r} not in PM family: needs scanner Binance fetch wiring "
                f"(bn_* / basis_*) or transforms runtime layer — next infra task.")
        row[c] = pm[c]
    return pd.DataFrame([row])


# ---- self-test --------------------------------------------------------------
if __name__ == '__main__':
    from .expr_eval_v1 import evaluate

    cs = 1771027200  # 2026-02-14 00:00 UTC Saturday → is_weekend=1, dow=5
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

    # H5 actual expr
    df = compute_row('p_intra_60_up<0.445 & is_weekend==1', ticks_up, ticks_dn, cs)
    assert df.iloc[0]['p_intra_60_up'] == 0.40
    assert df.iloc[0]['is_weekend'] == 1
    assert evaluate('p_intra_60_up<0.445 & is_weekend==1', df).tolist() == [True]

    # _dn side
    df = compute_row('p_intra_60_dn>0.555', ticks_up, ticks_dn, cs)
    assert df.iloc[0]['p_intra_60_dn'] == 0.60
    assert evaluate('p_intra_60_dn>0.555', df).tolist() == [True]

    # max_intra_X_up (grid-based — same value as old raw-tick for this input)
    df = compute_row('max_intra_120_up<0.6', ticks_up, ticks_dn, cs)
    assert df.iloc[0]['max_intra_120_up'] == 0.50

    # delta_intra_X
    df = compute_row('delta_intra_60_up<-0.05', ticks_up, ticks_dn, cs)
    assert abs(df.iloc[0]['delta_intra_60_up'] - (-0.10)) < 1e-9

    # Newly supported (compute.py 全 PM 集): hour_bucket / pre features
    df = compute_row('hour_bucket==0', ticks_up, ticks_dn, cs)
    assert df.iloc[0]['hour_bucket'] == 0
    assert evaluate('hour_bucket==0', df).tolist() == [True]

    df = compute_row('mean_pre_300_up<0.5', ticks_up, ticks_dn, cs)  # 不 crash 即可
    # 该 input ticks_up 在 cs 之前无数据, mean_pre_300_up = NaN, 比较 → False
    assert evaluate('mean_pre_300_up<0.5', df).tolist() == [False]

    # Friday weekday
    fri = 1770940800  # 2026-02-13 Friday
    df = compute_row('is_friday==1 & is_weekend==0', ticks_up, ticks_dn, fri)
    assert evaluate('is_friday==1 & is_weekend==0', df).tolist() == [True]

    # Old no-suffix names rejected by validate (BASE_COLS only has _up/_dn 后缀)
    for old_name in ('p_intra_60<0.445', 'max_intra_120<0.4', 'delta_intra_60<-0.05'):
        try:
            compute_row(old_name, ticks_up, ticks_dn, cs)
            raise AssertionError(f"should have raised: {old_name!r}")
        except (NotImplementedError, ValueError):
            pass

    # Still raise: transforms (stateful) / bn_ / basis_ (need Binance fetch)
    for bad in ('p_intra_60_up__zs24h>2',
                'bn_chg_pct_pre_300>0',
                'basis_pre_60_up>0'):
        try:
            compute_row(bad, ticks_up, ticks_dn, cs)
            raise AssertionError(f"should have raised: {bad!r}")
        except (NotImplementedError, ValueError):
            pass

    print("features: OK (delegates to compute.py SSOT, full PM family supported)")
