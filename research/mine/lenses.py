"""1-pred selection lenses — policy over the policy-free measurements table (NS1 refactor).

Context: compute.py emits raw per-(candidate × window) facts with NO gate. Selection lives HERE:
  multiple lenses, each a UNIFORM pass/fail predicate (fixed hurdle from gates.py — NOT per-factor
  tuned; per-factor threshold pick = in-sample overfit / p-hacking). A candidate is kept (∪) if it
  passes ANY edge lens; lens_count = how many edge lenses = trust signal (∩ core = all pass = most
  robust). mech_venue is a TAG (cross-venue, for the composite/top-down stage), NOT an edge gate —
  kept OUT of lens_count.

Source: scratch/data/measurements_<run_at>.parquet (LONG: candidate × window)
Expected: per-candidate frame {direction, lens_*, lens_count, passed, net_base, effN, wf_*, ...}.
  Persisting / dedup / paper_candidates write = select.py (next file); this only evaluates.

Derivation SSOT (formulas live here once; lenses + any diagnostic call them, no re-hand-coding —
  same discipline as friction.py; avoids silent drift):
  base1    = wr/ep − 1                       gross per-$1 lower bound (convexity-free)
  net_base = base1 − backtest_friction(ep)   net base; >0 ⇒ edge not reliant on 1/ep convexity
  effN     = (Σ_wins pay)²/Σ_wins pay²       participation ratio ∈[1,n_win] (compute emits); small ⇒
                                             few cheap wins carry nev (tail). eff_frac = effN/n_win
  direction= argmax(nev_up, nev_dn)          chosen once on full window; all lenses use that side
"""
from __future__ import annotations
import glob
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, '/home/polymarket_work')
from polybot.lib.friction import backtest_friction_ratio
from polybot.lib.gates import (
    BT_CROSS_BUCKET_NET_EV, NET_BASE_MIN, WILSON_Z,
    WF_MIN_WEEK_N, WF_MIN_WEEKS, WF_SIGN_FRAC,
    EP_BAND_LO, EP_BAND_HI,
)

DATA_DIR = Path('/home/polymarket_work/scratch/data')

# Cross-venue prefixes — binance leads PM price discovery (P5). TAG for the composite/top-down
# pool, NOT an edge gate. A name PATTERN, not a number → logic lives here, not gates.py.
MECH_VENUE_PREFIXES = ('bn_',)

_bfr = np.vectorize(backtest_friction_ratio)   # friction SSOT, vectorized for arrays


# ───────────────────────────── derivation SSOT ─────────────────────────────

def base1(wr, ep):
    """Gross per-$1 lower bound (convexity-free): wr/ep − 1."""
    return wr / ep - 1.0


def net_base(wr, ep):
    """Net tail-agnostic edge floor: base1 − friction(ep). Same friction basis as nev."""
    return base1(wr, ep) - _bfr(ep)


def wilson_lower(wr, n, z=WILSON_Z):
    """Wilson score LOWER bound of a binomial proportion wr=k/n at confidence z. n-aware and
    robust at wr→0/1 (Wald breaks there): shrinks small-n / extreme-wr toward conservative,
    → wr as n→∞. Feeds an n-aware net_base so selection-bias flukes fail the magnitude gate."""
    wr = np.asarray(wr, float); n = np.maximum(np.asarray(n, float), 1.0)
    denom = 1.0 + z * z / n
    center = wr + z * z / (2 * n)
    margin = z * np.sqrt(wr * (1 - wr) / n + z * z / (4 * n * n))
    return (center - margin) / denom


# ───────────────────────────── lens application ─────────────────────────────

def _walk_forward(m: pd.DataFrame, dirs: pd.DataFrame) -> pd.DataFrame:
    """Per-candidate sign-consistency of nev_d over COUNTABLE weekly windows (n ≥ WF_MIN_WEEK_N).
    Returns wf_n_weeks (countable weeks) + wf_sign_frac (fraction with nev_d > 0)."""
    wk = m[(m.window != 'full') & (m.n_hit >= WF_MIN_WEEK_N)].merge(dirs, on='expr')
    nev_d = np.where(wk.direction.values == 'up', wk.nev_up.values, wk.nev_dn.values)
    wk = wk.assign(pos=(nev_d > 0).astype(int))
    g = wk.groupby('expr').agg(wf_n_weeks=('pos', 'size'), _pos=('pos', 'sum'))
    g['wf_sign_frac'] = g['_pos'] / g['wf_n_weeks']
    return g[['wf_n_weeks', 'wf_sign_frac']].reset_index()


def apply_lenses(m: pd.DataFrame) -> pd.DataFrame:
    """LONG measurements → one row per candidate with direction, per-lens flags, lens_count,
    ∪ keep, + review metrics. Pure: no IO, no persistence (select.py does that)."""
    full = m[m.window == 'full'].copy()

    # Direction once, on full window: bet the side with higher full-window nev.
    up = full.nev_up.values >= full.nev_dn.values
    full['direction'] = np.where(up, 'up', 'dn')
    full['nev_d'] = np.where(up, full.nev_up.values, full.nev_dn.values)
    full['wr_d'] = np.where(up, full.wr.values, 1.0 - full.wr.values)
    full['ep_d'] = np.where(up, full.ep_up.values, full.ep_dn.values)
    full['net_base'] = net_base(full.wr_d.values, full.ep_d.values)        # point (diagnostic)
    # effN folded to chosen side (compute emits per-direction). Diagnostic only — NOT a lens gate:
    # in-sample tail flag (small effN ⇒ few cheap wins carry nev); proof stays paper-t.
    full['effN'] = np.where(up, full.effN_up.values, full.effN_dn.values)
    full['eff_frac'] = full.effN.values / np.maximum(full.wr_d.values * full.n_hit.values, 1.0)
    # n-aware net_base (PRIMARY gate + rank key): shrink wr to its Wilson lower bound first →
    # small-n / wr→1 selection-bias flukes the point estimate hides fail the floor. The lens
    # gates AND ranks on this; point net_base kept only as the pre-Wilson (un-shrunk) diagnostic.
    full['wr_wilson'] = wilson_lower(full.wr_d.values, full.n_hit.values)
    full['net_base_wilson'] = net_base(full.wr_wilson.values, full.ep_d.values)

    # Edge lenses (uniform hurdles from gates.py).
    full['lens_nev'] = full.nev_d.values >= BT_CROSS_BUCKET_NET_EV
    full['lens_net_base'] = full.net_base_wilson.values > NET_BASE_MIN     # n-aware (Wilson-shrunk wr)

    wf = _walk_forward(m, full[['expr', 'direction']])
    full = full.merge(wf, on='expr', how='left')
    full['wf_n_weeks'] = full['wf_n_weeks'].fillna(0).astype(int)
    full['wf_sign_frac'] = full['wf_sign_frac'].fillna(0.0)
    full['lens_walk_forward'] = (full.wf_n_weeks >= WF_MIN_WEEKS) & (full.wf_sign_frac >= WF_SIGN_FRAC)

    # mech_venue = TAG (not an edge lens; for composite/top-down pool, OUT of lens_count).
    full['tag_mech_venue'] = full.col.str.startswith(MECH_VENUE_PREFIXES)

    # fit-for-size: even-money ep band. Excludes longshot (variance/unreliable) + favorite (tiny
    # return / wr→1) — both ruin-risk for small undiversified single-factor capital. The BAND, not
    # the rank metric, excludes longshots (nev & net_base both carry δ/ep leverage).
    full['ep_band'] = pd.cut(full.ep_d, [0, EP_BAND_LO, EP_BAND_HI, 1.0],
                             labels=['longshot', 'even', 'fav'])
    full['fit_size'] = (full.ep_d >= EP_BAND_LO) & (full.ep_d <= EP_BAND_HI)

    edge = ['lens_nev', 'lens_net_base', 'lens_walk_forward']
    full['lens_count'] = full[edge].sum(axis=1).astype(int)
    full['passed'] = (
        np.where(full.lens_nev, 'nev,', '')
        + np.where(full.lens_net_base, 'net_base,', '')
        + np.where(full.lens_walk_forward, 'walk_forward,', '')
    )
    full['passed'] = full['passed'].str.rstrip(',')

    cols = ['expr', 'col', 'comp', 'thresh', 'entry_time_s', 'direction',
            'n_hit', 'wr_d', 'wr_wilson', 'ep_d', 'ep_band', 'fit_size', 'nev_d',
            'net_base', 'net_base_wilson', 'effN', 'eff_frac',
            'wf_n_weeks', 'wf_sign_frac', *edge,
            'tag_mech_venue', 'lens_count', 'passed']
    return full[cols].reset_index(drop=True)


def latest_measurements() -> Path:
    return Path(max(glob.glob(str(DATA_DIR / 'measurements_*.parquet')), key=os.path.getmtime))


def main():
    f = latest_measurements()
    print(f'measurements: {f.name}')
    m = pd.read_parquet(f)
    c = apply_lenses(m)
    n = len(c)
    print(f'candidates: {n}')
    print()
    print('=== per-lens pass count ===')
    for ln in ('lens_nev', 'lens_net_base', 'lens_walk_forward'):
        print(f'  {ln:18s}: {int(c[ln].sum()):6d} ({c[ln].mean():.1%})')
    print(f'  tag_mech_venue    : {int(c.tag_mech_venue.sum()):6d}  [tag, not edge]')
    print()
    print('=== ep_band × lens_count (fit_size = even-money band only) ===')
    print(pd.crosstab(c.ep_band, c.lens_count, margins=True).to_string())
    print()
    fit = c[c.fit_size & (c.lens_count == 3)].sort_values('net_base_wilson', ascending=False)
    print(f'=== fit-for-size survivors (even-money ∩ all-3-lens, net_base Wilson-gated), ranked '
          f'by NET_BASE_WILSON: {len(fit)} total (pre-dedup), top 20 ===')
    show = ['direction', 'n_hit', 'wr_d', 'wr_wilson', 'ep_d', 'nev_d', 'net_base',
            'net_base_wilson', 'effN', 'eff_frac', 'wf_sign_frac', 'tag_mech_venue', 'expr']
    with pd.option_context('display.width', 200, 'display.max_colwidth', 50):
        print(fit[show].head(20).to_string(index=False))

    # Land survivors (∪ keep, ranked by net_base). fit-for-size = fit_size & lens_count==3.
    # Ephemeral artifact for review/select.py; SSOT remains paper_candidates (post-dedup).
    keep = c[c.lens_count >= 1].sort_values('net_base_wilson', ascending=False)
    out = DATA_DIR / 'lens_candidates.parquet'
    keep.to_parquet(out, index=False)
    print(f'\nwrote {out}  ({len(keep)} rows ∪ keep; '
          f'{int((c.fit_size & (c.lens_count == 3)).sum())} fit-for-size)')


if __name__ == '__main__':
    main()
