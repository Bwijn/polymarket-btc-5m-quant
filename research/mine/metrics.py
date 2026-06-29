"""Factor scoring metrics — SSOT for the measurement ALGOS (gate-free).

Context: metric 算法之前散落在 compute.py / lenses.py / 各 scorecard 脚本里 → silent drift
  (静默漂移) 风险 (friction.py 同款教训: PM rate 0.072→0.07 只靠偶然 probe 抓到). 这里收口成
  唯一定义. 三根 SSOT 柱子分工: friction.py=成本模型(scanner 也用) / gates.py=政策阈值 /
  本模块=测量算法(research-only). 本模块 import 前两者的常量, 不重复定义.
Source: 被 cvd_scorecard.py (及未来任何 family scorecard) import; 数据来自 features.parquet.

铁律: 本模块 ZERO gate 常量. gate=policy 留 gates.py; metric=measurement, gate-free —— 这正是
  「gate 会逐 cycle churn 但测量数学不变」能成立的前提. 加 metric = 多一个函数, 不碰已有的.

Metric 注册表 (= 列 SSOT; 每 metric 的 coverage 分母不同, 手动遴选要兼听):
  metric            formula                                      coverage 分母
  ────────────────  ───────────────────────────────────────────  ──────────────
  nev               mean(win/(ep+d) − 1) − fee(mean_ep+d)         n_hit  (有效触发)
  base1             wr/ep − 1                                     n_hit  (point)
  net_base          base1 − fee(ep+d)                             n_hit
  wilson_lower      binomial proportion 下界 @z                    n_hit
  net_base_wilson   net_base(wilson_lower(wr,n), ep)              n_hit
  eff_n             (Σpay)²/Σpay²,  pay = 1/(ep+d) − 1            n_win  (仅赢单)
  drop5_nev         删 k 笔最低-ep 赢单后重算 nev                    n_hit − k
  wf_sign_frac      占比{周(n≥min) | 该周 nev>0}                    n_wk   (周)
  valid_fraction    n_hit / n_raw                                 n_raw  (全触发)
  d = BT_TO_PAPER_DRIFT (ep-space drift); fee = backtest_friction_ratio (= PM taker on ep+d).
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, '/home/polymarket_work')
from polybot.lib.friction import backtest_friction_ratio
from polybot.lib.gates import BT_TO_PAPER_DRIFT, WILSON_Z, WF_MIN_WEEK_N

DRIFT = BT_TO_PAPER_DRIFT


def nev(win, ep, drift=DRIFT):
    """net EV per $1 (primary, tail-敏感). win=0/1 outcome, ep=该向 entry price. 分母=n_hit."""
    return float((win / (ep + drift) - 1.0).mean() - backtest_friction_ratio(ep.mean()))


def base1(wr, ep):
    """gross per-$1 下界 (convexity-free): wr/ep − 1. point estimate, 分母=n_hit."""
    return wr / ep - 1.0


def net_base(wr, ep):
    """net tail-agnostic 底座: base1 − fee(ep+d). >0 ⇒ edge 不靠 1/ep 凸性. 分母=n_hit."""
    return float(base1(wr, ep) - backtest_friction_ratio(ep))


def wilson_lower(wr, n, z=WILSON_Z):
    """Wilson score 下界 of 二项比例 wr=k/n @z. n-aware, wr→0/1 处比 Wald 稳. 分母=n_hit."""
    n = max(float(n), 1.0)
    denom = 1.0 + z * z / n
    center = wr + z * z / (2 * n)
    margin = z * np.sqrt(wr * (1 - wr) / n + z * z / (4 * n * n))
    return (center - margin) / denom


def net_base_wilson(wr, n, ep):
    """n-aware net_base 地板: 先把 wr 收到 Wilson 下界再算 net_base. 我方真 floor. 分母=n_hit."""
    return net_base(wilson_lower(wr, n), ep)


def eff_n(ep_win, drift=DRIFT):
    """participation ratio (Σpay)²/Σpay², pay=赢单 per-$1 payoff. 小 ⇒ 少数便宜赢单撑 (tail). 分母=n_win."""
    if len(ep_win) == 0:
        return np.nan
    pay = 1.0 / (ep_win + drift) - 1.0
    return float(pay.sum() ** 2 / max((pay ** 2).sum(), 1e-12))


def drop5_nev(win, ep, drift=DRIFT, k=5):
    """删 k 笔最便宜(最低 ep)赢单后重算 nev. 转负 ⇒ edge 靠运气(几笔凸性赢单). 分母=n_hit−k. n_win≤k→NaN."""
    win_mask = win > 0.5
    if int(win_mask.sum()) <= k:
        return np.nan
    cheap = np.argsort(np.where(win_mask, ep, np.inf))[:k]   # k lowest-ep winners
    keep = np.ones(len(win), bool)
    keep[cheap] = False
    return nev(win[keep], ep[keep], drift)


def wf_sign_frac(win, ep, week, drift=DRIFT, min_week_n=WF_MIN_WEEK_N):
    """walk-forward 时序稳: 占比{可数周(n≥min_week_n) | 该周 nev>0}. 返回 (sign_frac, n_wk). 分母=n_wk."""
    df = pd.DataFrame({'wk': week, 'win': win, 'ep': ep})
    n_wk, pos = 0, 0
    for _, g in df.groupby('wk'):
        if len(g) < min_week_n:
            continue
        n_wk += 1
        pos += int(nev(g.win.values, g.ep.values, drift) > 0)
    return (pos / n_wk if n_wk else np.nan), n_wk


def valid_fraction(n_hit, n_raw):
    """有效触发占比 = n_hit / n_raw. 1−vfrac = 实盘 NaN-ep 稀释比例 (book_ask 仍下单但无 edge). 分母=n_raw."""
    return n_hit / n_raw if n_raw else np.nan
