"""CVD family factor scorecard — fresh metrics from features.parquet (the SSOT).

Context: 上 cycle 的 measurements_*/lens_candidates parquet 是 artifact (烤进上 cycle gate
  常量 lens_nev=0.10 等). 新 cycle 选 factor 必须从 SSOT=features.parquet 现算. 手动遴选要
  兼听全 metric, 而每 metric 的 coverage (覆盖样本数) 不同 — 故每行同时给 coverage 列:
    nev/wr/ep   分母 = n_hit  (signal fires AND ep_up&ep_dn 都非 NaN)
    effN/drop5  分母 = n_win  (n_hit 里的赢单)
    wf_sign     分母 = n_wk   (周, 每周 n≥WF_MIN_WEEK_N 才计)
    valid_frac  分母 = n_raw  (signal fires, 不论 ep 是否有效) ← 实盘 NaN-dilution
Source: research/data/features.parquet (per-event: cid/cs/up_won/p_intra_30_{up,dn}/bn_cvd_*).
Expected: stdout 横铺台 (每 col × tail 一行, 全 metric) + research/data/cvd_scorecard.parquet
  (cols × 2 tails × depth-grid, 全 metric; DuckDB/Wrangler 手动 dedup/遴选用).

Math 严格照搬 compute.py + lenses.py (照搬的是数学/成本模型, NOT 上 cycle 的 pass/fail gate):
  nev   = mean_valid(win/(ep+drift) − 1) − fee(mean_ep + drift)      [drift in ep-space, both terms]
  net_base = wr/ep − 1 − fee(ep+drift)                              [point, convexity-free 底座]
  nbw   = net_base(wilson_lower(wr,n), ep)                          [n-aware floor]
  effN  = (Σ_win pay)²/Σ_win pay²,  pay = 1/(ep+drift) − 1          [participation ratio]
  drop5 = nev after删 5 笔最便宜(最低 ep)赢单                         [order-stat tail probe]

L1/L2 busy-inclusive fold (4k-cap bias 校正, 2026-06-29):
  NaN-ep 触发按 et-grid recovery 分两类: L1=全 et NaN (真 illiquid, live freshness-filter 合法
  skip → 不算稀释); L2=et30 NaN 但晚 et 恢复 (4k-cap 截断的 busy candle, live 无 cap → 强制纳入).
  up_won 永远已知 (=BTC 结算, 非 ep) → 即便 et30 ep 丢, busy candle 赢没赢仍知.
  fold 列 = L2 用其 recover-et ep 当 proxy fold 回 (valid∪L2 = live-traded set):
    nb_fold/nev_fold = busy-inclusive net_base/nev (worst-case: 全 recoverer 当 L2).
    nb_fold>0 撑住 ⇒ 4k-cap 偏倚没吃掉底座 ⇒ 准入安全; 跌破 0 ⇒ family marginal.
  caveat: L2 真 et30 ep 永久丢 (cap 截), 晚 et ep 当 proxy → 时点偏移, 但 wr 稀释主导, 符号稳.
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, '/home/polymarket_work')
sys.path.insert(0, '/home/polymarket_work/research/mine')
import metrics as mx                       # metric-algo SSOT — 不再手抄 nev/net_base/wilson/...

FEATURES = '/home/polymarket_work/research/data/features.parquet'
OUT = '/home/polymarket_work/research/data/cvd_scorecard.parquet'
ET = 30                                   # CVD obs_t=0 → snap_to_entry_grid → 30 (ep = p_intra_30)
ET_GRID = [30, 45, 60, 75, 90, 120, 150, 180, 240, 270]   # recovery scan: first et w/ both ep valid
GRID_COLS = [f'p_intra_{t}_{s}' for t in ET_GRID for s in ('up', 'dn')]
DEPTHS = [0.01, 0.02, 0.03, 0.05, 0.10]   # tail percentile grid (low: q; high: 1−q)
REF_DEPTH = 0.02                          # main-table reference cut (≈ verdict deep tail)

CVD_COLS = [f'bn_cvd_pre_{w}{suf}' for w in (60, 300, 900)
            for suf in ('', '__zs24h', '__zs7d', '__rank24h', '__lag1')]


def compute_factor(df, col, comp, thresh):
    """Full metric vector for one (col, comp, thresh) candidate at et=30. Direction = argmax(nev)."""
    sig = df[col].values
    fire = (sig < thresh) if comp == '<' else (sig > thresh)   # NaN sig → False (excluded)
    n_raw = int(fire.sum())
    if n_raw == 0:
        return None
    up_p = df['p_intra_30_up'].values
    dn_p = df['p_intra_30_dn'].values
    valid = fire & ~np.isnan(up_p) & ~np.isnan(dn_p)
    n_hit = int(valid.sum())
    if n_hit < 1:
        return None
    upw = df['up_won'].values[valid]
    epu, epd = up_p[valid], dn_p[valid]
    wk = df['_week'].values[valid]

    def side(win, ep, stale):
        wr, m_ep = float(win.mean()), float(ep.mean())
        n_win = int((win > 0.5).sum())
        effN = mx.eff_n(ep[win > 0.5])                          # 分母 = n_win
        wf_sign, n_wk = mx.wf_sign_frac(win, ep, wk)            # 分母 = n_wk (周)
        return dict(nev=mx.nev(win, ep), wr=wr, ep=m_ep,
                    net_base=mx.net_base(wr, m_ep), nbw=mx.net_base_wilson(wr, len(win), m_ep),
                    effN=effN, eff_frac=effN / n_win if n_win else np.nan,
                    drop5=mx.drop5_nev(win, ep), n_win=n_win, wf=wf_sign, n_wk=n_wk,
                    stale=np.nanmedian(stale[valid]) if stale is not None else np.nan)

    su = side(upw, epu, df['p_intra_30_up_staleness_s'].values)
    sd = side(1.0 - upw, epd, df['p_intra_30_dn_staleness_s'].values)
    direction, s = ('up', su) if su['nev'] >= sd['nev'] else ('dn', sd)

    # busy-inclusive fold: L1/L2 split via et-recovery (df['_status'] precomputed in main).
    # valid = n_hit (=status 'valid'); L2 = et30 NaN→晚 et 恢复 (busy); L1 = 全 et NaN (illiquid, live skip).
    status = df['_status'].values
    L2 = fire & (status == 'L2')
    L1 = fire & (status == 'L1')
    n_L2, n_L1 = int(L2.sum()), int(L1.sum())
    win_all = df['up_won'].values if direction == 'up' else (1.0 - df['up_won'].values)
    ep_fold_all = (df['_epuf'] if direction == 'up' else df['_epdf']).values
    fold = fire & (status != 'L1')                              # valid∪L2 = live-traded set
    wf_win, wf_ep = win_all[fold], ep_fold_all[fold]
    wr_fold, ep_fold = float(wf_win.mean()), float(wf_ep.mean())
    fold_x = dict(n_L2=n_L2, n_L1=n_L1,
                  wr_L2=round(float(win_all[L2].mean()), 4) if n_L2 else np.nan,
                  wr_fold=round(wr_fold, 4), ep_fold=round(ep_fold, 4),
                  nb_fold=round(mx.net_base(wr_fold, ep_fold), 4),
                  nev_fold=round(mx.nev(wf_win, wf_ep), 4))
    return dict(col=col, comp=comp, thresh=round(float(thresh), 3), dir=direction,
                n_raw=n_raw, n_hit=n_hit, vfrac=round(n_hit / n_raw, 3),
                nev_opp=round(sd['nev'] if direction == 'up' else su['nev'], 4), **fold_x,
                **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()})


def main():
    df = pd.read_parquet(FEATURES, columns=['cs', 'up_won',
        'p_intra_30_up_staleness_s', 'p_intra_30_dn_staleness_s', *GRID_COLS, *CVD_COLS])

    # per-candle et30 status (signal-agnostic): first et w/ both ep valid → valid(30)/L2(>30)/L1(never).
    # L2 fold-ep = ep at that first-recover et (proxy for 4k-cap-lost et30 price).
    n = len(df)
    first_valid = np.full(n, -1)
    epuf, epdf = np.full(n, np.nan), np.full(n, np.nan)
    filled = np.zeros(n, bool)
    for t in ET_GRID:
        u, d = df[f'p_intra_{t}_up'].values, df[f'p_intra_{t}_dn'].values
        ok = (~np.isnan(u)) & (~np.isnan(d)) & (~filled)
        first_valid[ok], epuf[ok], epdf[ok], filled[ok] = t, u[ok], d[ok], True
    df['_status'] = np.where(first_valid == 30, 'valid', np.where(first_valid == -1, 'L1', 'L2'))
    df['_epuf'], df['_epdf'] = epuf, epdf
    df['_week'] = pd.to_datetime(df['cs'], unit='s').dt.to_period('W').dt.start_time
    print(f'features.parquet rows={len(df)}, CVD cols={len(CVD_COLS)}')
    print(f'et30 status (all events): {dict(df["_status"].value_counts())}\n')

    rows = []
    for col in CVD_COLS:
        sig = df[col].values
        sig = sig[~np.isnan(sig)]
        if len(sig) < 500:
            continue
        for q in DEPTHS:
            for comp, thr in (('<', np.quantile(sig, q)), ('>', np.quantile(sig, 1 - q))):
                r = compute_factor(df, col, comp, thr)
                if r:
                    r['depth'] = q
                    rows.append(r)
    sc = pd.DataFrame(rows)
    sc.to_parquet(OUT, index=False)

    show = ['col', 'comp', 'dir', 'n_raw', 'n_hit', 'n_L2', 'n_L1', 'vfrac',
            'wr', 'net_base', 'nev', 'wr_L2', 'nb_fold', 'nev_fold', 'nbw', 'drop5', 'wf']
    pd.set_option('display.width', 280); pd.set_option('display.max_columns', 40)
    pd.set_option('display.max_rows', 120)

    print('=' * 130)
    print(f'TABLE A — main scorecard (every col, both tails, REF depth p{int(REF_DEPTH*100):02d}); sort=nbw (display only)')
    print('=' * 130)
    a = sc[sc.depth == REF_DEPTH].sort_values('nbw', ascending=False)
    print(a[show].to_string(index=False))

    print('\n' + '=' * 130)
    print('TABLE B — threshold robustness: nev across tail depths (favorable dir per col×tail)')
    print('=' * 130)
    piv = sc.pivot_table(index=['col', 'comp', 'dir'], columns='depth', values='nev')
    piv.columns = [f'nev@p{int(c*100):02d}' for c in piv.columns]
    cnt = sc.pivot_table(index=['col', 'comp', 'dir'], columns='depth', values='n_hit')
    piv['n@p01'] = cnt[0.01].astype('Int64'); piv['n@p10'] = cnt[0.10].astype('Int64')
    print(piv.sort_values('nev@p02', ascending=False).to_string())

    print(f'\nwrote {OUT}  ({len(sc)} rows = cols × 2 tails × {len(DEPTHS)} depths)')
    print('coverage 提示: nev/wr/ep 分母=n_hit; effN/drop5 分母=n_win; wf 分母=n_wk; vfrac 分母=n_raw.')


if __name__ == '__main__':
    main()
