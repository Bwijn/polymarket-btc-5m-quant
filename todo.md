# Polybot TODO

## NOW

- **R2** — paper-only, live 累计 +$2.73 USD 锁定。继续 paper 至 cap/8 周自然 KILL。
- **R4** — paper-only, 继续收 sample, paper 决定 graduate/KILL。
- **2pred_rep_20260526 (4 reps)** — Phase B mining 出, dedup correlation cluster 后 4 个真独立信号入 `paper_candidates` cycle_tag=`2pred_rep_20260526`。下一步: 进 `polybot/strategies.py` ACTIVE + deploy paper。
  - R1: `delta_intra_60_dn>0.18 & max_intra_30_dn__zs7d>1.67` DOWN  v2_nev 9.71%
  - R2: `delta_intra_60_up<-0.28 & delta_intra_30_up<-0.11` DOWN  v2_nev 8.88%
  - R3: `max_intra_30_up>0.74 & delta_intra_60_dn__zs7d<-0.89` UP  v2_nev 8.84%
  - R4: `delta_intra_60_dn__zs7d<-0.48 & mean_intra_30_up>0.61` UP  v2_nev 7.25%
  - correlation: R1↔R2 / R3↔R4 内 corr 0.55-0.59 (sub-mechanism), cross direction ~0
  - 全 ep ≈ 0.83 (favorite side, fee 1.2%), cs+60 entry, 顺势 confirm 非 contrarian

## NEXT (顺序)

- **[NEXT-1 CRITICAL] mining nev formula → per-$1 PnL semantics (systemic bug from cycle 1)**
  - 现状: mining `nev = wr - mep` 是 **per-share** PnL (bet 1 share, win 拿 $1, share价 $ep → PnL $(1-ep))
  - 应改: PM 实际 bet $1 capital, 拿 1/ep shares → per-$1 PnL = (1-ep)/ep if won else -1
  - **Magnitude 缩水**: ep=0.5 缩 2×, ep=0.7 缩 1.43×, ep=0.83 缩 1.20× (+Jensen 效应更大)
  - 系统性 under-rank low-ep (underdog) strategies. R4 mining 3.34% 真 PnL 10.53%
  - 跟 paper / settle / compute_drift 算法一直不一致 (paper 一直用真 PnL)
  - **Fix scope (Path A 彻底):**
    1. `scratch/research/mine_gpu.py` Phase A/B 改 per-event PnL formula:
       ```python
       pnl = cp.where(up_won, (1-ep_up)/cp.maximum(ep_up, 1e-10), -1)
       gross_ev = sum(M_iv * pnl[None,:]) / n_hit
       ```
    2. `polybot/lib/gates.py` thresholds recalibrate:
       BT_CROSS_BUCKET_NET_EV: 0.07 (mining nev) → 0.10 (true PnL nev) — TBD by re-mine
       PAPER_TO_LIVE_NET_EV: 0.05 → clarify (已经是 per-$1 因 paper 用真 PnL ✓)
    3. Re-mine 全 historical cycles (paper_pick7, 2pred, cross_era_relaxed) → true PnL nev
    4. Re-INSERT paper_candidates with new cycle_tag, drop stale rows
    5. Verify deploy.sh hook: 加 differential test 确保 mining / paper / drift formula 三处一致
  - **R-series decision review** (基于 true PnL bt):
    - R4: true_pnl bt +10.53% / paper +13.66% ✓ — **NOT KILL, 之前 wrong call**
    - R2: true_pnl bt +1.93% / paper +12% — marginal noise, keep paper
    - H5: true_pnl bt -2.00% — killed ✓
  - 等 user 主动开作业 (compact session 后)

- **[NEXT-2] paper graduation 等 sample**
  - R4 BN features 一直工作, n=69 → 200 估 1 个月
  - R2 + P1-P4 已修 (scanner pmtrades integration 完成), 开始累 sample
  - P3 + P4 同 candle co-fire 已观察 (corr 0.59 sub-mechanism, 符合预期)

- **[NEXT-3] Local end-to-end test workflow** — 不允许跳过
  - 改 ws / scanner runtime 后必须: `cd polybot && PYTHONPATH=.. uv run python main.py`
    跑 1-2 candle, grep ERROR, 无再 deploy
  - Deploy = production verification only, 不再 debug 迭代

- **[NEXT-4] build_features 改 incremental** — 30-45 min → 30-60s (~50× 加速)
  - 每 source builder 加 "read existing, compute only missing (cid,cs)"; transforms 需 feed last 2016 events 作 context
  - 触发: 下次 ingest cycle 觉得 45 min 不能忍

- **[NEXT-5] gate-check 代码化** — 每 factor 算 n/EV/std/t, checkpoint 自动判 GRADUATE/KILL
  - 加 db 表 `factor_paper_progress` + script 自动评
  - 触发: paper 有新 factor 入 ACTIVE 后

- **[NEXT-6] transforms.py SSOT-ify** — mining batch pandas vs polybot per-event 不同 paradigm
  - 当前 windows/min_periods constants 已 delegate (TRANSFORM_SPEC), math impl 仍 parallel
  - 选项: (a) polybot 加 batch compute_zs_batch / compute_rank_batch, mining 用之; (b) 加 differential test catch byte-diff
  - 不阻塞 — pandas rolling vs polybot math 实测 byte-equal (verify_compute_ssot 已 cover)

## ML PLAN B (如 NEXT-1 Phase B 仍 0 cross-bucket, 启动)

- **LightGBM** 找 non-linear interaction → feature importance → 翻译回 rule 验证 (`ml_methods.md` §5)
- rule mining 永远不退场, LightGBM 只做 feature discovery
- 数据扩到 100K+ events 才考虑 MLP / Transformer (现 28K 太少)

## INFRASTRUCTURE

- **httpx async + Clash proxy 长连接 wedge** — 已知问题, backfill v3 改 subprocess curl 绕开 (future HTTP IO 同样模式)
- **旧 VPS (荃湾) ~05-31 到期** — cold standby fallback

## OPEN QUESTIONS

- Q1. paper > bt 7-8% gap (R2/R4 sample 噪声 OR 真 regime favor recent events)?
  - 第三因子 H5 sample 最大 (156) gap 仅 +3%, 暗示噪声主导
  - 等更多 paper data 后深查
- Q2. Transform 历史查询: 跨大 gap 拉 stale data, 加 cs ≥ now-N*300 时间窗?
