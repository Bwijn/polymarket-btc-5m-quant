# Polybot TODO

## NOW — 主线: 89 underdog survivors 决死活

- **89 cross-bucket survivors (per-$1 + P5 fix)** — `scratch/data/survivors_cross_1779856573.parquet` (5/27)
  - underdog rebound family: ep≈0.22, cs+120 entry, 87 UP / 2 DOWN, top v2_nev 35%
  - TOP1: `min_intra_120_dn>0.58` UP
  - ⚠️ **edge 最近 4 周塌**: weekly nev Feb-Apr 55-80% (t 2-3.5) → Apr27-May24 ≈0% (只靠 05-04 单周 +121% outlier 撑, 去掉≈0)
  - linear decay slope t=0.71 不显著 → 不是干净 decay, 像 recent regime death
  - per-trade std=2.59 (ep~0.22 lottery), paper 旧触发率 4-9 月才测准 magnitude
  - **决策 (待定)**: (1) features 截止 05-24 已 stale 8 天 → extend→06-01 + 重挖确认死活 [倾向]; (2) drop family; (3) dedup→1-2 rep $20/单 sniff test
  - 任何决策前 features 必须先 extend (现建在 8 天前数据上)

- **R4 = 唯一存活 factor, GRADUATE 候选** (per-$1 re-eval + paper 双验, 2026-06-01)
  - DOWN, et=0, even-money ep≈0.51, `bn_taker_buy_ratio_pre_300>0.76 & bn_vol_zscore_pre_60__zs24h>0.37`
  - **per-$1 bt V2(OOS) +19.9% ↔ paper +21.2%** (n=126, t=2.52, +$43) — drift-fix 验证: bt 终于预测 paper
  - 过 paper→live gate (t>1.65 ∧ nev≥5%). 决策: 直接上 live / 再收 2 周收窄 magnitude CI (下界擦 5%)?
  - paper_candidates 现仅此 1 行 (per_dollar_20260601)
- **R2/P1/P2/P3/P4 = KILLED** (factor_decisions 2026-06-01, per-$1 OOS + paper 双弱)
  - favorite ep~0.8 overfit: 样本内 10-15% → V2 OOS 2-5.5% → paper -4.8%~+1.4% 全 insignificant
  - 解了 Open Q1: paper-bt gap 不是 regime, 是旧 per-share bt 虚高; per-$1 后 bt↔paper 一致
  - ⚠️ 需 deploy 才真停 VPS paper (strategies.py ACTIVE 已改, db 已 kill)

## NEXT (顺序)

- **[NEXT-1] commit per-$1 + P5 公式修复 (3 文件 uncommitted)**
  - friction.py / gates.py / compute/pmtrades.py — P2 (per-$1) + P5 (drift 加 ep 非常数) 已改完 self-test 过
  - mine_gpu.py (scratch) 同步改完
  - 待 commit, 别悬空 (SSOT 级改动)

- **[NEXT-2] deploy.sh differential test: mining/paper/drift 三处公式一致** (原 NEXT-1 fix scope #5)
  - 加 hook 确保 per-$1 PnL formula 不再 drift

- **[NEXT-3] R4 graduate 决策** — 过 paper→live gate (t=2.52, nev 21.2%). 直接 live / 再收 2 周收窄 CI 下界 (擦 5% hurdle)

- **[NEXT-4] Local end-to-end test workflow** — 不允许跳过
  - 改 ws / scanner runtime 后必须: `cd polybot && PYTHONPATH=.. uv run python main.py`
    跑 1-2 candle, grep ERROR, 无再 deploy
  - Deploy = production verification only, 不再 debug 迭代

- **[NEXT-5] build_features 改 incremental** — 30-45 min → 30-60s (~50× 加速)
  - 每 source builder 加 "read existing, compute only missing (cid,cs)"; transforms 需 feed last 2016 events 作 context
  - 触发: 下次 ingest cycle 觉得 45 min 不能忍

- **[NEXT-6] drop `hypothesis` 列 (彻底弃 abbreviation, expr-only SSOT)** — VPS production migration, 早晚删
  - 顺序: ① scanner.py 改 dedup key + 写库用 `expr` 不用 `strat.id`; ② VPS polybot.db `ALTER TABLE paper_trade_5m_binary DROP COLUMN hypothesis`; ③ 本地 sync
  - 阻塞前提: 现 scanner 用 strat.id 当 (id,cs) dedup + hypothesis 列. expr 已 1:1 可替 (100% 填充验证过)
  - 未来 Strategy 不再起 R/P 缩写, id=expr

- **[NEXT-7] transforms.py SSOT-ify** — mining batch pandas vs polybot per-event 不同 paradigm
  - 当前 windows/min_periods constants 已 delegate (TRANSFORM_SPEC), math impl 仍 parallel
  - 选项: (a) polybot 加 batch compute_zs_batch / compute_rank_batch, mining 用之; (b) 加 differential test catch byte-diff
  - 不阻塞 — pandas rolling vs polybot math 实测 byte-equal (verify_compute_ssot 已 cover)

## ML PLAN B (如重挖 Phase B 仍 0 cross-bucket, 启动)

- **LightGBM** 找 non-linear interaction → feature importance → 翻译回 rule 验证 (`ml_methods.md` §5)
- rule mining 永远不退场, LightGBM 只做 feature discovery
- 数据扩到 100K+ events 才考虑 MLP / Transformer (现 28K 太少)

## INFRASTRUCTURE

- **httpx async + Clash proxy 长连接 wedge** — 已知问题, backfill v3 改 subprocess curl 绕开 (future HTTP IO 同样模式)
- **旧 VPS (荃湾) ~05-31 到期** — cold standby fallback

## OPEN QUESTIONS

- ~~Q1. paper > bt gap~~ **RESOLVED 2026-06-01**: 旧 per-share bt 虚高造成假 gap; per-$1 修复后 bt↔paper 一致 (R4 bt 19.9%↔paper 21.2%). 非 regime.
- Q2. Transform 历史查询: 跨大 gap 拉 stale data, 加 cs ≥ now-N*300 时间窗?
