"""DSL v1 — code IS the spec. PM BTC 5m factor expression parser + validator + evaluator.

This file is the SSOT. There is no companion markdown.
Changing DSL = changing this file. Reading DSL = reading this file.

Grammar (BNF):
    expr        := predicate (' & ' predicate)*
    predicate   := atom comparator term
    term        := atom | value
    atom        := base_col ('__' transform)*
    base_col    := one of BASE_COLS
    transform   := op[arg]                  # op + arg concatenated, no internal _
    op          := one of OPS keys
    arg         := <int> | <number><unit>   # e.g. 1, 24h, 7d
    comparator  := one of COMPARATORS
    value       := signed float/int

Public API:
    canonicalize(expr) -> str       # whitespace + AND normalization
    validate(expr) -> dict          # parse to AST or raise ValueError
    evaluate(expr, df) -> Series    # bool mask on feature DataFrame
"""
from __future__ import annotations
import re
from typing import Any

import pandas as pd

# ---- frozen DSL constants (改这里 = 改 DSL) ------------------------------------

BASE_COLS = frozenset({
    'asym_3600', 'chg_rate_intra_120', 'chg_rate_intra_300',
    'chg_rate_pre_1800', 'chg_rate_pre_3600', 'chg_rate_pre_900',
    'delta_intra_120', 'delta_intra_15', 'delta_intra_150', 'delta_intra_180',
    'delta_intra_240', 'delta_intra_270', 'delta_intra_30', 'delta_intra_45',
    'delta_intra_60', 'delta_intra_75', 'delta_intra_90',
    'delta_pre_120', 'delta_pre_1800', 'delta_pre_300', 'delta_pre_3600',
    'delta_pre_60', 'delta_pre_600', 'delta_pre_900',
    'dist_fair_open', 'dow', 'hour_bucket', 'hour_utc',
    'is_friday', 'is_saturday', 'is_sunday', 'is_weekend',
    'max_intra_120', 'max_intra_180', 'max_intra_240', 'max_intra_30',
    'max_intra_300', 'max_intra_60', 'max_intra_90',
    'mean_intra_120', 'mean_intra_180', 'mean_intra_240', 'mean_intra_30',
    'mean_intra_300', 'mean_intra_60', 'mean_intra_90',
    'mean_pre_180', 'mean_pre_1800', 'mean_pre_300', 'mean_pre_3600',
    'mean_pre_60', 'mean_pre_600', 'mean_pre_900',
    'min_intra_120', 'min_intra_180', 'min_intra_240', 'min_intra_30',
    'min_intra_300', 'min_intra_60', 'min_intra_90',
    'minute_of_hour',
    'p_intra_120', 'p_intra_15', 'p_intra_150', 'p_intra_180',
    'p_intra_240', 'p_intra_270', 'p_intra_30', 'p_intra_45',
    'p_intra_60', 'p_intra_75', 'p_intra_90',
    'p_open',
    'p_pre_0', 'p_pre_120', 'p_pre_1800', 'p_pre_30', 'p_pre_300',
    'p_pre_3600', 'p_pre_60', 'p_pre_600', 'p_pre_900',
    'rng_intra_120', 'rng_intra_180', 'rng_intra_240', 'rng_intra_30',
    'rng_intra_300', 'rng_intra_60', 'rng_intra_90',
    'rng_pre_180', 'rng_pre_1800', 'rng_pre_300', 'rng_pre_3600',
    'rng_pre_60', 'rng_pre_600', 'rng_pre_900',
    'slope_diff_300_1800',
    'slope_pre_180', 'slope_pre_1800', 'slope_pre_300', 'slope_pre_3600',
    'slope_pre_60', 'slope_pre_600', 'slope_pre_900',
    'std_intra_120', 'std_intra_180', 'std_intra_240', 'std_intra_30',
    'std_intra_300', 'std_intra_60', 'std_intra_90',
    'std_pre_180', 'std_pre_1800', 'std_pre_300', 'std_pre_3600',
    'std_pre_60', 'std_pre_600', 'std_pre_900',
    'vol_ratio_900_3600', 'we_hour',
    'z_pre_1800', 'z_pre_300', 'z_pre_3600', 'z_pre_900',
})

# op spec: arg_type ∈ {'window', 'int', None}; arg_set None = 任意值, 否则白名单
OPS: dict[str, dict[str, Any]] = {
    'zs':   {'arg_type': 'window', 'arg_set': frozenset({'1h', '2h', '12h', '24h', '3d', '7d'})},
    'rank': {'arg_type': 'window', 'arg_set': frozenset({'1h', '2h', '12h', '24h', '3d', '7d'})},
    'lag':  {'arg_type': 'int',    'arg_set': None},
    'abs':  {'arg_type': None},
    'sign': {'arg_type': None},
}

COMPARATORS = frozenset({'<', '>', '<=', '>=', '==', '!='})

# regex helpers (compiled once)
_RE_AND       = re.compile(r'\s*&\s*')
_RE_WS        = re.compile(r'\s+')
_RE_NUMBER    = re.compile(r'^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$')  # incl. 1.1e-05
_RE_TRANSFORM = re.compile(r'^([a-z_]+?)(\d+[a-z]?)?$')  # op[arg]


# ---- canonicalize ---------------------------------------------------------------

def canonicalize(expr: str) -> str:
    """Normalize expr字面: trim, collapse whitespace, ' & ' canonical AND.

    Idempotent. Two equivalent expr → same string. Used as PK in candidates.
    """
    s = expr.strip()
    s = _RE_WS.sub(' ', s)
    s = _RE_AND.sub(' & ', s)
    # 围绕 comparator 不强行加空格 (写 'a<0.5' / 'a < 0.5' 都接受, 输出去空格)
    # canonical 形式: comparator 两侧无空格, 字符比较符 (<, >, <=, >=, ==, !=)
    # 这样 PK 紧凑, 跟当前主流写法一致 ('p_intra_60<0.445')
    for c in ('<=', '>=', '==', '!=', '<', '>'):
        s = re.sub(r'\s*' + re.escape(c) + r'\s*', c, s)
    return s


# ---- parser ---------------------------------------------------------------------

def _parse_atom(tok: str) -> dict:
    """Parse 'p_intra_60' or 'p_intra_60__zs24h__lag1' → {col, transforms}."""
    parts = tok.split('__')
    col = parts[0]
    if col not in BASE_COLS:
        raise ValueError(f"unknown base column: {col!r} (not in BASE_COLS)")
    transforms = []
    for t in parts[1:]:
        m = _RE_TRANSFORM.match(t)
        if not m:
            raise ValueError(f"malformed transform: {t!r}")
        op = m.group(1).rstrip('_')
        arg = m.group(2)
        if op not in OPS:
            raise ValueError(f"unknown op: {op!r} (not in OPS keys {list(OPS)})")
        spec = OPS[op]
        if spec['arg_type'] is None:
            if arg is not None:
                raise ValueError(f"op {op!r} takes no arg, got {arg!r}")
        else:
            if arg is None:
                raise ValueError(f"op {op!r} requires arg")
            if spec.get('arg_set') is not None and arg not in spec['arg_set']:
                raise ValueError(f"op {op!r} arg {arg!r} not in {sorted(spec['arg_set'])}")
            if spec['arg_type'] == 'int' and not arg.isdigit():
                raise ValueError(f"op {op!r} expects int arg, got {arg!r}")
        transforms.append({'op': op, 'arg': arg})
    return {'col': col, 'transforms': transforms}


def _parse_term(tok: str) -> dict:
    """Term is either a number (literal) or an atom (column ref).

    Returns {'kind': 'value', 'value': float} or {'kind': 'atom', ...}.
    """
    if _RE_NUMBER.match(tok):
        return {'kind': 'value', 'value': float(tok)}
    a = _parse_atom(tok)
    return {'kind': 'atom', **a}


def _split_predicate(pred: str) -> tuple[str, str, str]:
    """Find the comparator in pred, split into (lhs, comp, rhs).

    Tries 2-char comparators first (<=, >=, ==, !=), then 1-char (<, >).
    """
    for c in ('<=', '>=', '==', '!='):
        i = pred.find(c)
        if i >= 0:
            return pred[:i].strip(), c, pred[i + 2:].strip()
    for c in ('<', '>'):
        i = pred.find(c)
        if i >= 0:
            return pred[:i].strip(), c, pred[i + 1:].strip()
    raise ValueError(f"no comparator in predicate: {pred!r}")


def validate(expr: str) -> dict:
    """Parse + validate. Raise ValueError if invalid. Return AST.

    AST shape:
        {'predicates': [
            {'lhs': <atom>, 'comp': str, 'rhs': <term>},
            ...
        ]}
    where <atom> = {'col', 'transforms'} and <term> = {'kind': 'value'|'atom', ...}.
    """
    if not expr or not expr.strip():
        raise ValueError("empty expr")
    canon = canonicalize(expr)
    preds = canon.split(' & ')
    if not preds:
        raise ValueError(f"no predicates: {expr!r}")
    if len(preds) > 3:
        raise ValueError(f"too many predicates ({len(preds)}); max 3 per expr")
    out = []
    for p in preds:
        lhs_tok, comp, rhs_tok = _split_predicate(p)
        if comp not in COMPARATORS:
            raise ValueError(f"bad comparator {comp!r}")
        lhs = _parse_atom(lhs_tok)
        rhs = _parse_term(rhs_tok)
        out.append({'lhs': lhs, 'comp': comp, 'rhs': rhs})
    return {'predicates': out}


# ---- evaluator ------------------------------------------------------------------

def _eval_atom(atom: dict, df: pd.DataFrame) -> pd.Series:
    """Apply transforms to a base column. Currently only no-transform path implemented.
    Transforms (zs/rank/lag/abs/sign) raise NotImplementedError until features_v14 builds them.
    """
    if atom['transforms']:
        # 暂未实现 transform 求值. mining 用到时再开 features_v14 物化 + 这里 dispatch.
        ops = '__'.join(t['op'] + (t.get('arg') or '') for t in atom['transforms'])
        raise NotImplementedError(f"transform chain {ops!r} not yet evaluable; "
                                  "materialize columns into features_v14.parquet first")
    return df[atom['col']]


def _comp_apply(lhs: pd.Series, comp: str, rhs: pd.Series | float) -> pd.Series:
    if comp == '<':  return lhs < rhs
    if comp == '>':  return lhs > rhs
    if comp == '<=': return lhs <= rhs
    if comp == '>=': return lhs >= rhs
    if comp == '==': return lhs == rhs
    if comp == '!=': return lhs != rhs
    raise ValueError(f"bad comparator: {comp!r}")  # 不该到这, validate 保证


def evaluate(expr: str, df: pd.DataFrame) -> pd.Series:
    """Compute boolean mask. NaN → False (predicate misses)."""
    ast = validate(expr)
    mask = None
    for p in ast['predicates']:
        lhs = _eval_atom(p['lhs'], df)
        rhs = p['rhs']['value'] if p['rhs']['kind'] == 'value' else _eval_atom(p['rhs'], df)
        m = _comp_apply(lhs, p['comp'], rhs)
        m = m.fillna(False)
        mask = m if mask is None else (mask & m)
    return mask


# ---- self-test (跑这个文件即跑 test) -------------------------------------------

if __name__ == '__main__':
    # canonicalize idempotency
    assert canonicalize('p_intra_60<0.445') == 'p_intra_60<0.445'
    assert canonicalize('p_intra_60 < 0.445') == 'p_intra_60<0.445'
    assert canonicalize('p_intra_60<0.445 & is_weekend==1') == 'p_intra_60<0.445 & is_weekend==1'
    assert canonicalize('p_intra_60<0.445&is_weekend==1') == 'p_intra_60<0.445 & is_weekend==1'
    assert canonicalize('  p_intra_60  <  0.445  &  is_weekend == 1  ') == 'p_intra_60<0.445 & is_weekend==1'
    assert canonicalize(canonicalize('p_intra_60 < 0.445')) == canonicalize('p_intra_60 < 0.445')

    # validate happy path (4 当前 cluster expr)
    assert validate('p_intra_60<0.445 & is_weekend==1')['predicates']
    assert validate('max_intra_120<0.4')['predicates']
    assert validate('p_intra_60>0.555 & is_friday==1')['predicates']
    assert validate('min_intra_90>0.6')['predicates']

    # column-vs-column predicate
    ast = validate('slope_pre_300<slope_pre_600')
    assert ast['predicates'][0]['rhs']['kind'] == 'atom'

    # transform path (validate ok 但 evaluate 不实现)
    ast = validate('p_intra_60__zs24h>2.0')
    assert ast['predicates'][0]['lhs']['transforms'][0] == {'op': 'zs', 'arg': '24h'}

    # validate 拒绝
    rejected = [
        '',                              # 空
        'unknown_col<0.5',               # 未知 base col
        'p_intra_60__bogus5h<0.5',       # 未知 op
        'p_intra_60__zs99h<0.5',         # arg 不在白名单
        'p_intra_60 == ',                # 缺 rhs
        'p_intra_60<0.445 | is_weekend==1',  # OR 不支持
        'p_intra_60<0.445 & p_open<0.5 & is_weekend==1 & dow==0',  # >3 predicate
    ]
    for r in rejected:
        try:
            validate(r)
            raise AssertionError(f"should reject: {r!r}")
        except (ValueError, NotImplementedError):
            pass

    # evaluate on a tiny synthetic df
    df = pd.DataFrame({
        'p_intra_60': [0.4, 0.5, 0.3, 0.6],
        'is_weekend': [0, 1, 1, 0],
        'p_open':     [0.5, 0.5, 0.5, 0.5],
    })
    mask = evaluate('p_intra_60<0.445 & is_weekend==1', df)
    assert mask.tolist() == [False, False, True, False], mask.tolist()
    mask = evaluate('p_intra_60<p_open', df)
    assert mask.tolist() == [True, False, True, False], mask.tolist()

    print("expr_eval_v1: OK")
    print(f"  BASE_COLS: {len(BASE_COLS)} cols")
    print(f"  OPS: {list(OPS)}")
    print(f"  COMPARATORS: {sorted(COMPARATORS)}")
