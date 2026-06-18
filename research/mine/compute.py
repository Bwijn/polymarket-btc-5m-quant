"""1-pred brute compute — policy-free measurements (NS1 refactor; replaces mine_gpu Phase A).

Context: mine_gpu (MVP) conflated compute+selection (nev gate 焊进 GPU pass) and ran an
  exhaustive Phase B 2-pred pairing (combinatorial 爆炸 + FWE death + statistically self-
  defeating). NS1 refactor splits the two: this file does COMPUTE ONLY — enumerate every
  1-pred candidate and emit its FULL metric vector WITHOUT any gate. Selection (nev / net-base
  / walk-forward / mech-venue lenses, ∪) lives downstream in lenses.py + select.py.
  Composites (was Phase B) move to guided search (LightGBM/gplearn), built ON this 1-pred base.

Source: research/data/features.parquet  (INPUT CONTRACT enforced by _validate_invariants)
Expected: research/data/measurements_<run_at>.parquet — LONG: one row per (candidate × window),
  NO filter beyond a noise floor (full-window n_hit ≥ MIN_N_HIT_ABS).

Key design choices (each a conscious decision, not a default):
  - NO gate: noise floor (n≥MIN_N_HIT_ABS) is a RESOURCE/sanity bound, NOT a statistical filter.
    "nev > X" becomes one downstream lens. Compute = mechanism; selection = policy (separated).
  - Walk-forward by PERIOD (replaces V1/V2 cutover): per-candidate per-window {n,wr,ep,nev} so the
    regime oscillation is visible downstream. Cutover was misaligned (April trough 93% in V1,
    V2 ≈ May peak → gate read edge at a peak). Default freq='W' (week): only ~4.5mo of data since
    PM 5m launched Feb → monthly = 4-5 windows (too few for consistency); weekly ≈ 16. Per-week n
    is small/noisy → downstream walk-forward lens judges SIGN consistency, not per-week magnitude.
  - Threshold GRID from full-sample quantiles = ENUMERATION (where to cut), NOT selection.
    OOS rigor lives in the downstream walk-forward lens ("survives held-out recent months"),
    decoupled from threshold derivation.
  - BOTH directions emitted (nev_up, nev_dn) per row — direction choice is downstream (policy-free).
  - per-$1 PnL + ep-space drift, identical semantics to mine_gpu (paper SSOT aligned).
  - effN = (Σ_wins payoff)²/Σ_wins payoff² (participation ratio) emitted here — it is sum-based
    (GPU-cheap), so a fact like nev. drop5 stays survivor-only downstream: it is an ORDER statistic
    (needs the top-5 individual wins), NOT reconstructable from aggregate sums → re-reads raw there.
"""
from __future__ import annotations
import argparse
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import cupy as cp

sys.path.insert(0, '/home/polymarket_work')
from polybot.lib.friction import backtest_friction_ratio
from polybot.lib.gates import MIN_N_HIT_ABS, BT_TO_PAPER_DRIFT

FEATURES = Path('/home/polymarket_work/research/data/features.parquet')
OUT_DIR = Path('/home/polymarket_work/research/data')

MAX_ENTRY_TIME_S = 270
# et=0/15 dropped (too sparse for entry-price proxy; confirmed coverage probe 2026-05-23).
ENTRY_TIME_GRID = [30, 45, 60, 75, 90, 120, 150, 180, 240, 270]
ENTRY_T_TO_IDX = {t: i for i, t in enumerate(ENTRY_TIME_GRID)}
N_GRID = len(ENTRY_TIME_GRID)
PERCENTILES_GPU = cp.asarray(np.array([i / 100 for i in range(1, 100)], dtype=np.float32))

_FLOW_IMB_RE     = re.compile(r'pmt_flow_imb_5s_(\d+)_')      # past-only [target-2, target]
_SPREAD_PROXY_RE = re.compile(r'pmt_spread_proxy_(\d+)_')     # past-only [target-15, target]

_bfr_vec = np.vectorize(backtest_friction_ratio)             # SSOT friction, vectorized for arrays


def derive_obs_time(col: str) -> int:
    """col 名 → observation time (s 距 cs); decides entry_t lower bound. New families MUST
    register explicitly or default obs_t=0 silently leaks look-ahead (surfaced by validate)."""
    base = col.split('__')[0]
    parts = base.split('_')
    for i, p in enumerate(parts):
        if p == 'intra' and i + 1 < len(parts):
            try:
                return int(parts[i + 1])
            except ValueError:
                continue
    m = _FLOW_IMB_RE.search(base)
    if m:
        return int(m.group(1))
    m = _SPREAD_PROXY_RE.search(base)
    if m:
        return int(m.group(1))
    return 0


def snap_to_entry_grid(t: int) -> int:
    """Snap obs time to next grid entry (entry must be at/after observation)."""
    for g in ENTRY_TIME_GRID:
        if g >= t:
            return g
    return ENTRY_TIME_GRID[-1]


def classify_arr(arr_cpu: np.ndarray) -> str:
    """Numeric 1D classification (NaN-aware): binary | categorical | continuous | skip."""
    if arr_cpu.dtype.kind not in 'fiub':
        return 'skip'
    nonnull = arr_cpu[~np.isnan(arr_cpu)] if arr_cpu.dtype.kind == 'f' else arr_cpu
    if len(nonnull) < 500:
        return 'skip'
    n_unique = len(np.unique(nonnull))
    if n_unique == 2:
        return 'binary'
    if arr_cpu.dtype.kind in 'iub' and n_unique <= 24:
        return 'categorical'
    if arr_cpu.dtype.kind == 'f' and n_unique >= 30:
        return 'continuous'
    return 'skip'


def _validate_invariants(df: pd.DataFrame, feat_cols: list[str]) -> None:
    """Enforce INPUT CONTRACT. Raise on hard violation, warn on soft. Run BEFORE compute."""
    print('  validate_invariants...')
    required = {'cid', 'cs', 'era', 'up_won'}
    required |= {f'p_intra_{t}_{s}' for t in ENTRY_TIME_GRID for s in ('up', 'dn')}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"missing required cols: {sorted(missing)}")
    if df['up_won'].isna().sum() > 0:
        raise ValueError("up_won has NaN — would be silently treated as up-loss")
    worst = []
    for t in ENTRY_TIME_GRID:
        for s in ('up', 'dn'):
            col = f'p_intra_{t}_{s}'
            rate = df[col].isna().mean()
            worst.append((rate, col))
            if rate > 0.80:
                raise ValueError(f"{col} NaN rate {rate:.1%} > 80% hard limit — check backfill")
    worst.sort(reverse=True)
    if worst[0][0] > 0.30:
        print("    ⚠️ entry NaN > 30% worst:")
        for rate, col in worst[:5]:
            if rate > 0.30:
                print(f"        {col}: {rate:.1%}")
    suspect_pat = re.compile(r'_(intra|flow_imb|spread_proxy)_')
    suspect = [c for c in feat_cols if derive_obs_time(c) == 0 and suspect_pat.search(c)]
    if suspect:
        print(f"    ⚠️ {len(suspect)} cols look intra/flow/spread but obs_t=0 (silent look-ahead):")
        for c in suspect[:10]:
            print(f"        {c}")
    print('  validate_invariants OK')


def build_windows(df: pd.DataFrame, freq: str = 'W') -> list[tuple[str, cp.ndarray]]:
    """Walk-forward windows = ['full'] + one per period ('W'=week default, 'M'=month).
    Weekly default: PM 5m market launched Feb → only ~4.5 months of data; monthly gives too
    few windows (4-5) to assess consistency. Weekly (~16) gives the downstream sign-consistency
    lens enough windows (per-week n is small/noisy → that lens judges SIGN, not magnitude).
    Returns [(label, float-mask (N,))]; 'full' = all events (each still gated by entry_valid)."""
    dt = pd.to_datetime(df['cs'].values, unit='s')
    labels = dt.to_period(freq).start_time.strftime('%Y%m%d')   # period-start date, sortable
    prefix = freq.lower()
    N = len(df)
    windows = [('full', cp.ones(N, dtype=cp.float32))]
    for lab in sorted(set(labels)):
        windows.append((f'{prefix}{lab}', cp.asarray((labels == lab).astype(np.float32))))
    return windows


class Measure1Pred:
    """1-pred brute, per-(candidate × window) policy-free measurements. No gate beyond noise floor."""

    def __init__(self, df: pd.DataFrame, feat_cols: list[str], log, freq: str = 'W'):
        self.N = len(df)
        self.log = log
        t0 = time.time()
        log(f"    GPU upload: N={self.N}, building arrays...")

        up_won_cpu = df['up_won'].values.astype(np.float32)
        assert not np.isnan(up_won_cpu).any(), "up_won NaN — run _validate_invariants upstream"
        self.up_won = cp.asarray(up_won_cpu)

        self.windows = build_windows(df, freq)
        log(f"    windows: {[w[0] for w in self.windows]}")

        # Entry-price grids (N_GRID, N) + entry_valid mask + per-$1 PnL term (mine_gpu semantics).
        self.p_entry_up = cp.zeros((N_GRID, self.N), dtype=cp.float32)
        self.p_entry_dn = cp.zeros((N_GRID, self.N), dtype=cp.float32)
        self.entry_valid = cp.zeros((N_GRID, self.N), dtype=cp.float32)
        self.pnl_term_up = cp.zeros((N_GRID, self.N), dtype=cp.float32)
        self.pnl_term_dn = cp.zeros((N_GRID, self.N), dtype=cp.float32)
        for i, t in enumerate(ENTRY_TIME_GRID):
            u = df[f'p_intra_{t}_up'].values.astype(np.float32)
            d = df[f'p_intra_{t}_dn'].values.astype(np.float32)
            valid = (~np.isnan(u)) & (~np.isnan(d))
            self.entry_valid[i] = cp.asarray(valid.astype(np.float32))
            self.p_entry_up[i] = cp.asarray(np.nan_to_num(u, nan=0.0))
            self.p_entry_dn[i] = cp.asarray(np.nan_to_num(d, nan=0.0))
            # per-$1 PnL: bet UP → up_won/ep_paper − 1; drift in ep-space (1/ep nonlinearity
            # haircuts underdog more). entry_valid=0 → term 0 (masked out of sums).
            ep_up_p = cp.maximum(self.p_entry_up[i] + BT_TO_PAPER_DRIFT, 1e-6)
            ep_dn_p = cp.maximum(self.p_entry_dn[i] + BT_TO_PAPER_DRIFT, 1e-6)
            self.pnl_term_up[i] = (self.up_won / ep_up_p - 1) * self.entry_valid[i]
            self.pnl_term_dn[i] = ((1 - self.up_won) / ep_dn_p - 1) * self.entry_valid[i]

        # Feature arrays (classifiable + obs_t < MAX only)
        self.feat_arrays: dict[str, cp.ndarray] = {}
        self.feat_meta: dict[str, dict] = {}
        n_skip_obs = n_skip_kind = 0
        for col in feat_cols:
            obs_t = derive_obs_time(col)
            if obs_t >= MAX_ENTRY_TIME_S:
                n_skip_obs += 1
                continue
            arr_cpu = df[col].values
            kind = classify_arr(arr_cpu)
            if kind == 'skip':
                n_skip_kind += 1
                continue
            self.feat_arrays[col] = cp.asarray(arr_cpu.astype(np.float32))
            et = snap_to_entry_grid(obs_t)
            self.feat_meta[col] = {'kind': kind, 'obs_t': obs_t,
                                   'entry_t': et, 'entry_idx': ENTRY_T_TO_IDX[et]}
        log(f"    upload done {time.time()-t0:.1f}s. kept={len(self.feat_meta)}, "
            f"skip_obs={n_skip_obs}, skip_kind={n_skip_kind}, "
            f"mem={cp.get_default_memory_pool().used_bytes()/1e9:.2f}GB")

    def run(self) -> list[dict]:
        """Emit LONG rows: one per (candidate × window) where full-window n_hit ≥ noise floor."""
        rows: list[dict] = []
        t0 = time.time()
        for col, meta in self.feat_meta.items():
            x = self.feat_arrays[col]
            eidx = meta['entry_idx']
            p_up, p_dn = self.p_entry_up[eidx], self.p_entry_dn[eidx]
            v = self.entry_valid[eidx]
            pt_up, pt_dn = self.pnl_term_up[eidx], self.pnl_term_dn[eidx]
            # effN payoff-on-wins (loss→0): pw = won·pt isolates the winning per-$1 payoff (1/ep−1).
            # Σpw and Σpw² are the sufficient stats for effN = (Σpw)²/Σpw² derived per window below.
            pw_up = self.up_won * pt_up
            pw_dn = (1 - self.up_won) * pt_dn
            pw_up_sq, pw_dn_sq = pw_up * pw_up, pw_dn * pw_dn

            if meta['kind'] == 'continuous':
                x_valid = x[~cp.isnan(x)]
                if len(x_valid) < 500:
                    continue
                thresholds = cp.unique(cp.quantile(x_valid, PERCENTILES_GPU))
                comps = ('<', '>')
            else:  # binary / categorical
                thresholds = cp.unique(x[~cp.isnan(x)])
                if len(thresholds) > 24:
                    continue
                comps = ('==',)

            for comp in comps:
                if comp == '<':
                    masks = x[None, :] < thresholds[:, None]
                elif comp == '>':
                    masks = x[None, :] > thresholds[:, None]
                else:
                    masks = x[None, :] == thresholds[:, None]
                masks_f = masks.astype(cp.float32) * v[None, :]      # (T,N), entry_valid applied

                # Per-window reductions → CPU stats keyed by window label.
                win_stats: dict[str, dict] = {}
                for wlabel, wmask in self.windows:
                    m = masks_f * wmask[None, :]
                    n = m.sum(axis=1)
                    won = (m * self.up_won[None, :]).sum(axis=1)
                    sep_up = (m * p_up[None, :]).sum(axis=1)
                    sep_dn = (m * p_dn[None, :]).sum(axis=1)
                    spnl_up = (m * pt_up[None, :]).sum(axis=1)
                    spnl_dn = (m * pt_dn[None, :]).sum(axis=1)
                    s1pw_up = (m * pw_up[None, :]).sum(axis=1)
                    s2pw_up = (m * pw_up_sq[None, :]).sum(axis=1)
                    s1pw_dn = (m * pw_dn[None, :]).sum(axis=1)
                    s2pw_dn = (m * pw_dn_sq[None, :]).sum(axis=1)
                    win_stats[wlabel] = {
                        'n': n.get(), 'won': won.get(),
                        'sep_up': sep_up.get(), 'sep_dn': sep_dn.get(),
                        'spnl_up': spnl_up.get(), 'spnl_dn': spnl_dn.get(),
                        's1pw_up': s1pw_up.get(), 's2pw_up': s2pw_up.get(),
                        's1pw_dn': s1pw_dn.get(), 's2pw_dn': s2pw_dn.get(),
                    }

                thr_h = thresholds.get()
                # Vectorized derived metrics per window (length T) — friction once per array,
                # not per scalar. NaN where window has no events (distinguishes 0-data from 0-edge).
                derived: dict[str, dict] = {}
                for wlabel, st in win_stats.items():
                    n = st['n']
                    safe = np.maximum(n, 1.0)
                    wr = st['won'] / safe
                    ep_up = st['sep_up'] / safe
                    ep_dn = st['sep_dn'] / safe
                    nev_up = st['spnl_up'] / safe - _bfr_vec(ep_up)
                    nev_dn = st['spnl_dn'] / safe - _bfr_vec(ep_dn)
                    # effN = (Σpw)²/Σpw² ∈ [1, n_win]: effectively how many equal-sized wins carry
                    # the edge; small ⇒ a few cheap wins dominate (tail mirage). nan when no wins.
                    effN_up = np.where(st['s2pw_up'] > 0,
                                       st['s1pw_up'] ** 2 / np.maximum(st['s2pw_up'], 1e-12), np.nan)
                    effN_dn = np.where(st['s2pw_dn'] > 0,
                                       st['s1pw_dn'] ** 2 / np.maximum(st['s2pw_dn'], 1e-12), np.nan)
                    empty = n < 1
                    for a in (wr, ep_up, ep_dn, nev_up, nev_dn, effN_up, effN_dn):
                        a[empty] = np.nan
                    derived[wlabel] = {'n': n, 'wr': wr, 'ep_up': ep_up,
                                       'ep_dn': ep_dn, 'nev_up': nev_up, 'nev_dn': nev_dn,
                                       'effN_up': effN_up, 'effN_dn': effN_dn}

                keep = np.where(win_stats['full']['n'] >= MIN_N_HIT_ABS)[0]
                for k in keep:
                    thr_val = int(thr_h[k]) if comp == '==' else float(thr_h[k])
                    expr = f'{col}{comp}{thr_val}'
                    for wlabel, d in derived.items():
                        rows.append({
                            'expr': expr, 'col': col, 'comp': comp, 'thresh': thr_val,
                            'entry_time_s': meta['entry_t'], 'obs_time_s': meta['obs_t'],
                            'window': wlabel, 'n_hit': int(d['n'][k]),
                            'wr': float(d['wr'][k]),
                            'ep_up': float(d['ep_up'][k]), 'ep_dn': float(d['ep_dn'][k]),
                            'nev_up': float(d['nev_up'][k]), 'nev_dn': float(d['nev_dn'][k]),
                            'effN_up': float(d['effN_up'][k]), 'effN_dn': float(d['effN_dn'][k]),
                        })
        self.log(f"    compute done {time.time()-t0:.1f}s, rows={len(rows)} "
                 f"(candidates={len(set(r['expr'] for r in rows))})")
        return rows


def write_parquet(rows: list[dict], run_at: int) -> Path | None:
    """rows → measurements_<run_at>.parquet (ephemeral; SSOT = downstream paper_candidates)."""
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df['run_at'] = run_at
    out = OUT_DIR / f'measurements_{run_at}.parquet'
    df.to_parquet(out, index=False, compression='zstd')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--max-cols', type=int, default=0,
                    help='If >0, randomly sample N feature cols (smoke test).')
    ap.add_argument('--freq', default='W', choices=['W', 'M'],
                    help='walk-forward window granularity: W=week (default), M=month.')
    args = ap.parse_args()

    print('=' * 70)
    print('compute.py — 1-pred brute, policy-free measurements (no gate, per-month)')
    print(f'           noise floor: full n_hit ≥ {MIN_N_HIT_ABS}')
    dev = cp.cuda.runtime.getDeviceProperties(0)
    print(f'           GPU: {dev["name"].decode()}, free={cp.cuda.runtime.memGetInfo()[0]/1e9:.1f}GB')
    print('=' * 70)

    df = pd.read_parquet(FEATURES)
    print(f'features.parquet: shape={df.shape}')

    skip_cols = {'p_open_up', 'p_open_dn', 'up_won', 'era', 'cid', 'cs',
                 'p_pre_0_up', 'p_pre_0_dn'}
    for t in ENTRY_TIME_GRID:
        skip_cols |= {f'p_intra_{t}_up', f'p_intra_{t}_dn',
                      f'p_intra_{t}_up_staleness_s', f'p_intra_{t}_dn_staleness_s'}
    feat_cols = sorted(c for c in df.columns if c not in skip_cols)
    if args.max_cols > 0:
        rng = np.random.default_rng(0)
        idx = sorted(rng.choice(len(feat_cols), size=min(args.max_cols, len(feat_cols)), replace=False))
        feat_cols = [feat_cols[i] for i in idx]
    print(f'feature cols: {len(feat_cols)}')

    _validate_invariants(df, feat_cols)

    def log(msg):
        print(msg, flush=True)

    t0 = time.time()
    run_at = int(time.time())
    rows = Measure1Pred(df, feat_cols, log, freq=args.freq).run()

    if not args.dry_run and rows:
        out = write_parquet(rows, run_at)
        print(f'\n  wrote {out}  ({len(rows)} rows)')

    print('=' * 70)
    print(f'compute.py DONE in {time.time()-t0:.0f}s, rows={len(rows)}')
    print('=' * 70)


if __name__ == '__main__':
    main()
