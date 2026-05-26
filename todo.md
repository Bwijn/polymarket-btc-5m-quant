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

- **[NEXT-1] paper graduation 等 sample**
  - R4 BN features 一直工作, n=69 → 200 估 1 个月
  - R2 + P1-P4 已修 (scanner pmtrades integration 完成), 开始累 sample
  - P3 + P4 同 candle co-fire 已观察 (corr 0.59 sub-mechanism, 符合预期)

- **[NEXT-2] Local end-to-end test workflow** — 不允许跳过
  - 改 ws / scanner runtime 后必须: `cd polybot && PYTHONPATH=.. uv run python main.py`
    跑 1-2 candle, grep ERROR, 无再 deploy
  - Deploy = production verification only, 不再 debug 迭代

- **[NEXT-2] build_features 改 incremental** — 30-45 min → 30-60s (~50× 加速)
  - 每 source builder 加 "read existing, compute only missing (cid,cs)"; transforms 需 feed last 2016 events 作 context
  - 触发: 下次 ingest cycle 觉得 45 min 不能忍

- **[NEXT-3] gate-check 代码化** — 每 factor 算 n/EV/std/t, checkpoint 自动判 GRADUATE/KILL
  - 加 db 表 `factor_paper_progress` + script 自动评
  - 触发: paper 有新 factor 入 ACTIVE 后

- **[NEXT-4] compute.py 拆 module + 全 family delegation 化** — 739 行单文件
  - 拆 `polybot/lib/compute.py` → `polybot/lib/compute/` package, 同步 features/pm + binance + basis 改 delegation
  - 不阻塞任何 NEXT

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
