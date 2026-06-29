"""Dedup new candidates vs the live/paper roster + each other (admission gate, NS1).

Context: a candidate may only take a paper slot if it is an INDEPENDENT signal — folding
  a re-skin of an incumbent inflates FWE (族系误差率) and, if same-direction, overbets
  (相关 edge 线性叠加 = 破真实 Kelly). Two orthogonal lenses (NS1 ⑤, 不同对象别混):
    Jaccard(trigger sets)  = 同一发现? (FWE/触发集重合)  — same-dir pairs only.
    return-corr(PnL stream) = 收益共动? (Kelly/overbet)   — direction-signed, auto-handles 反向=对冲.
  Reference points from strategies.py audit: INDEP = corr .15 / Jac .08 (#4 vs R4);
  REDUNDANT = corr .64 (#3 skipped). Verdict line ~0.5.
Source: research/data/features.parquet (SSOT) + polybot.strategies.ACTIVE (incumbent roster).
Expected: pairwise Jaccard + return-corr (candidates × roster + intra-candidate) + greedy
  rep verdict (seed reps = incumbents → candidate is NEW rep iff indep of all, else folded).
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, '/home/polymarket_work')
from polybot.strategies import ACTIVE
from polybot.lib.expr_eval_v1 import evaluate
from polybot.lib.gates import FACTOR_DEDUP_JACCARD_MAX as FOLD_J, BT_TO_PAPER_DRIFT as DRIFT
from polybot.lib.friction import backtest_friction_ratio

FEAT = '/home/polymarket_work/research/data/features.parquet'
ET_GRID = [30, 45, 60, 75, 90, 120, 150, 180, 240, 270]
CORR_MAX = 0.50                                  # return-corr fold line (between .15 indep / .64 redun)

# candidate CVD factors (p02 tail, dir per scorecard). thresh recomputed = scorecard parity.
CVD = [('cvd_pre_60→up',      'bn_cvd_pre_60',      '<', 0.02, 'up'),
       ('cvd_pre_300→dn',     'bn_cvd_pre_300',     '>', 0.02, 'dn'),
       ('cvd_pre_60_zs7d→up', 'bn_cvd_pre_60__zs7d','<', 0.02, 'up')]


def load():
    cols = {'cs', 'up_won'}
    cols |= {f'p_intra_{t}_{s}' for t in ET_GRID for s in ('up', 'dn')}
    for _, c, *_ in CVD:
        cols.add(c)
    # roster signal cols pulled lazily by evaluate() → load full superset via union w/ exprs
    df = pd.read_parquet(FEAT)
    # per-candle status + fold ep (= scorecard口径: first et w/ both ep valid → proxy for L2)
    n = len(df)
    fv = np.full(n, -1); epu = np.full(n, np.nan); epd = np.full(n, np.nan); done = np.zeros(n, bool)
    for t in ET_GRID:
        u, d = df[f'p_intra_{t}_up'].values, df[f'p_intra_{t}_dn'].values
        ok = (~np.isnan(u)) & (~np.isnan(d)) & (~done)
        fv[ok], epu[ok], epd[ok], done[ok] = t, u[ok], d[ok], True
    df['_L1'] = (fv == -1)
    df['_epu'], df['_epd'] = epu, epd
    return df


def factor_streams(df, name, expr, direction):
    """fire (condition bool) + per-candle net return stream (0 where not live-traded)."""
    fire = evaluate(expr, df).to_numpy(dtype=bool)
    fire = fire & ~np.isnan(df['_epu' if direction == 'up' else '_epd'].values) & ~df['_L1'].values
    won = df['up_won'].values if direction == 'up' else (1.0 - df['up_won'].values)
    ep = (df['_epu'] if direction == 'up' else df['_epd']).values
    r = np.zeros(len(df))
    epd = np.maximum(ep[fire] + DRIFT, 1e-6)
    r[fire] = won[fire] / epd - 1.0 - backtest_friction_ratio(ep[fire])
    return dict(name=name, dir=direction, fire=fire, ret=r, n=int(fire.sum()))


def main():
    df = load()
    facs = []
    for s in ACTIVE:                                          # incumbents first (= seed reps)
        d = 'up' if s.direction == 'UP' else 'dn'
        facs.append(dict(factor_streams(df, s.label, s.expr, d), incumbent=True, live=s.live))
    for name, col, comp, q, d in CVD:                         # candidates, scorecard nb_fold order
        sig = df[col].values; sig = sig[~np.isnan(sig)]
        thr = np.quantile(sig, q if comp == '<' else 1 - q)
        facs.append(dict(factor_streams(df, name, f'{col}{comp}{thr}', d), incumbent=False, live=False))

    N = len(facs)
    def jac(i, j):
        a, b = facs[i]['fire'], facs[j]['fire']
        u = int((a | b).sum())
        return int((a & b).sum()) / u if u else 0.0
    def cor(i, j):
        a, b = facs[i]['ret'], facs[j]['ret']
        if a.std() == 0 or b.std() == 0: return 0.0
        return float(np.corrcoef(a, b)[0, 1])

    inc = [i for i in range(N) if facs[i]['incumbent']]
    cand = [i for i in range(N) if not facs[i]['incumbent']]

    print(f"rows={len(df)}  incumbents={len(inc)} (R4 live + 4 paper)  candidates={len(cand)} (CVD)\n")
    print("=== candidate × roster: same-dir Jaccard / return-corr (★=fold-trigger) ===")
    for ci in cand:
        f = facs[ci]
        print(f"\n[{f['name']}]  dir={f['dir']}  n_fire={f['n']}")
        any_same = False
        for ii in inc:
            g = facs[ii]
            if g['dir'] != f['dir']:
                continue
            any_same = True
            J, C = jac(ci, ii), cor(ci, ii)
            flag = '★REDUNDANT' if (J > FOLD_J or C > CORR_MAX) else 'indep'
            tag = 'LIVE' if g['live'] else 'paper'
            print(f"    vs {g['name']:18s}({tag}) Jac={J:.3f}  corr={C:+.3f}  → {flag}")
        if not any_same:
            print(f"    (no same-dir incumbent — structurally novel direction/family)")

    print("\n=== intra-candidate (CVD vs CVD, same-dir) ===")
    for a in range(len(cand)):
        for b in range(a + 1, len(cand)):
            ia, ib = cand[a], cand[b]
            if facs[ia]['dir'] != facs[ib]['dir']:
                continue
            print(f"    {facs[ia]['name']:18s} ⟷ {facs[ib]['name']:18s} "
                  f"Jac={jac(ia, ib):.3f}  corr={cor(ia, ib):+.3f}")

    print("\n=== greedy verdict (seed reps = incumbents; candidate NEW rep iff indep of all reps) ===")
    reps = list(inc)
    for ci in cand:                                           # already in nb_fold order
        folded = None
        for ri in reps:
            if facs[ri]['dir'] != facs[ci]['dir']:
                continue
            if jac(ci, ri) > FOLD_J or cor(ci, ri) > CORR_MAX:
                folded = ri; break
        if folded is None:
            reps.append(ci)
            print(f"  ✅ INDEPENDENT  {facs[ci]['name']:18s} → admit to paper")
        else:
            print(f"  ❌ REDUNDANT    {facs[ci]['name']:18s} → folds into {facs[folded]['name']} "
                  f"(Jac={jac(ci, folded):.3f} corr={cor(ci, folded):+.3f})")
    print(f"\nFOLD_J={FOLD_J} (gates.py)  CORR_MAX={CORR_MAX}  DRIFT={DRIFT}")


if __name__ == '__main__':
    main()
