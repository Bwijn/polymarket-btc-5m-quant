"""Dedup review of the fit-for-size survivors (NS1): greedy rep-selection by Jaccard trigger overlap.

Context: collapse near-duplicate HYPOTHESES (FWE hygiene) before paper-test — keep one rep per group,
  宽进 (high threshold, only fold near-identical). Greedy leader-selection: sort by net_base_wilson desc,
  fold factor into an already-kept rep iff same-direction AND Jaccard(trigger sets) > FOLD_J. Jaccard
  (|A∩B|/|A∪B|, symmetric = "same trigger set") not containment (|A∩B|/min, asymmetric = "B⊂A special
  case") — containment over-folds a rare narrow factor into any broad one whose candles cover it.
  Trigger set = feature condition ∩ entry_valid (p_intra non-nan = actually tradeable candle).
  REVIEW tool (CSV + console); select.py productionizes (seed S=[R4], write paper_candidates).
Source: scratch/data/{features.parquet, lens_candidates.parquet}.
Expected: survivors_review.csv (cluster-annotated) + grouped console overview.
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, '/home/polymarket_work')
from polybot.lib.gates import FACTOR_DEDUP_JACCARD_MAX as FOLD_J   # Jaccard fold threshold (宽进)

FEAT = '/home/polymarket_work/scratch/data/features.parquet'
LENS = '/home/polymarket_work/scratch/data/lens_candidates.parquet'
CSV  = '/home/polymarket_work/scratch/data/survivors_review.csv'

cand = pd.read_parquet(LENS)
fit = (cand[(cand.fit_size) & (cand.lens_count == 3)]
       .sort_values('net_base_wilson', ascending=False).reset_index(drop=True))
print(f"fit-for-size survivors (even-money ∩ 3-lens, net_base Wilson-gated): {len(fit)}")

ets = sorted({int(t) for t in fit.entry_time_s})
need = set(fit.col)
for t in ets:
    need |= {f'p_intra_{t}_up', f'p_intra_{t}_dn'}   # for entry_valid (tradeable candles)
feat = pd.read_parquet(FEAT, columns=sorted(need))

def mask_of(r):                                       # trigger set = feature condition ∩ entry_valid
    x = feat[r.col].to_numpy(); v = ~np.isnan(x)
    if r.comp == '>':   cond = v & (x > r.thresh)
    elif r.comp == '<': cond = v & (x < r.thresh)
    else:               cond = v & (x == r.thresh)
    tradeable = ~np.isnan(feat[f'p_intra_{int(r.entry_time_s)}_{r.direction}'].to_numpy())
    return cond & tradeable

masks = [mask_of(r) for r in fit.itertuples()]
sizes = [int(m.sum()) for m in masks]
dirs  = fit.direction.tolist()

reps, cluster, ov = [], [None] * len(fit), [0.0] * len(fit)
for i in range(len(fit)):
    best_j, best_oc = None, 0.0
    for j in reps:
        if dirs[j] != dirs[i]: continue              # opposite bets ≠ same hypothesis
        inter = int(np.count_nonzero(masks[i] & masks[j]))
        oc = inter / max(1, sizes[i] + sizes[j] - inter)   # Jaccard = |∩| / |∪|
        if oc > best_oc: best_oc, best_j = oc, j
    if best_oc > FOLD_J:
        cluster[i], ov[i] = best_j, best_oc          # redundant → fold into rep best_j
    else:
        reps.append(i); cluster[i], ov[i] = i, best_oc  # new rep (best_oc = max ov vs same-dir reps)

def family(col):
    base = col.split('__')[0]
    for p in ('oi_chg_pct','bn_chg_pct','bn_','asym','pmt_','delta','chg_rate','slope',
              'p_pre','p_intra','std','rng','mean','min','max','vol','fund','ls_','basis','z_'):
        if base.startswith(p): return p.rstrip('_')
    return base.split('_')[0]

fit['family']       = fit.col.map(family)
fit['is_rep']       = [cluster[i] == i for i in range(len(fit))]
fit['cluster_rep']  = [fit.expr[cluster[i]] for i in range(len(fit))]
fit['rep_net_base'] = [fit.net_base[cluster[i]] for i in range(len(fit))]
fit['overlap_w_rep']= np.round(ov, 3)

out = fit[['family','expr','direction','n_hit','wr_d','ep_d','nev_d','net_base','effN','eff_frac',
           'wf_sign_frac','wf_n_weeks','tag_mech_venue','is_rep','cluster_rep','rep_net_base',
           'overlap_w_rep']].copy()
out = out.sort_values(['rep_net_base','is_rep','net_base'], ascending=[False,False,False])
out.to_csv(CSV, index=False, float_format='%.4f')
print(f"independent signals (clusters): {len(reps)}   → CSV: {CSV}\n")

# console overview: each cluster = rep line + folded members
for rk, ri in enumerate(reps, 1):
    members = [i for i in range(len(fit)) if cluster[i] == ri and i != ri]
    r = fit.iloc[ri]
    tag = 'bn' if r.tag_mech_venue else '  '
    print(f"[{rk:2d}] nbw={r.net_base_wilson:.3f} nb={r.net_base:.3f} nev={r.nev_d:.3f} "
          f"wr={r.wr_d:.2f}→{r.wr_wilson:.2f} ep={r.ep_d:.2f} n={int(r.n_hit):4d} "
          f"wf={r.wf_sign_frac:.2f} effN={r.effN:.0f}/{int(r.wr_d*r.n_hit)} {tag} {r.direction} {r.expr}"
          f"   (+{len(members)} variant)")
    for mi in members:
        m = fit.iloc[mi]
        print(f"       └ ov={ov[mi]:.2f} nb={m.net_base:.3f} {m.family:11s} {m.direction} {m.expr}")
