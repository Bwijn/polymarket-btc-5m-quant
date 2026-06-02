# Polybot TODO

## NOW — 5 个独立候选已入 paper_candidates, 待推进 paper

cycle `per_dollar_20260602`: 199 cross-bucket survivors → dedup → **5 独立信号** (2 underdog + 3 mid, 3 up + 2 dn). 机制: min_intra_120_dn / min_intra_180_up / chg_rate_120_up / chg_rate_120_dn / std_180_up.

**>>> NEXT STEP: 推 5 个进 paper — strategies.py 改 ACTIVE → 本地 e2e test (NEXT-1, 不可跳) → deploy → scanner 实跑. paper 数据当裁判 (self-prove + live 候补 bench). <<<**

看板 1 行 SQL: `SELECT * FROM paper_candidates WHERE cycle_tag='per_dollar_20260602'` (db/pm_btc5m.db)

**已决/背景** (别重walk):
- **dedup 方法固化**: gates.py `FACTOR_DEDUP_OVERLAP_MAX=0.45` (bimodal valley) + `FACTOR_DEDUP_CAPEFF_TIEBREAK=0.15`; 脚本 `scratch/research/dedup_survivors.py` (overlap-coef 聚类 + freq×nev capeff guard, V2 HR). 唯一 filter = 去 correlation, 不设数量 cap (独立 signal 全进 paper). 三桶 nev 都直接信用 — drift 在 ep-space 已对 underdog 保守 haircut (gates.BT_TO_PAPER_DRIFT).
- **R4 = 旧 cycle (per_dollar_20260601) live 候选** — paper t=2.6 nev21.7% n=127 过 gate, GRADUATE 决策待拍 (NEXT-2).
- **R2/P1-P4 = KILLED** (factor_decisions, per-$1 OOS+paper 双弱). ACTIVE=(R4,).
- 看板: sync 后 `SELECT * FROM paper_active_agg` (db/polybot_live.db) = 当前 ACTIVE factor 实盘.

## NEXT (顺序)

- **[NEXT-1] Local e2e test (推 paper / 改 scanner 前不可跳)** — `cd polybot && PYTHONPATH=.. uv run python main.py` 跑 1-2 candle, grep ERROR, 无再 deploy. Deploy = production verification only, 不再 debug 迭代.

- **[NEXT-2] R4 graduate 决策** — 过 paper→live gate (t=2.6, nev21.7%, n=127). 直接 live / 再收 2 周收窄 CI 下界 (擦 5% hurdle).

- **[NEXT-3] drop `hypothesis` 列 (expr-only SSOT)** — VPS migration: ① scanner.py dedup key 用 `expr` 不用 strat.id; ② VPS `ALTER TABLE paper_trade_5m_binary DROP COLUMN hypothesis`; ③ 本地 sync. 未来 Strategy id=expr, 不起 R/P 缩写.

- **[NEXT-4] transforms.py SSOT-ify** — mining pandas vs polybot per-event 双实现. 不阻塞 (实测 byte-equal, verify_compute_ssot cover). 选项: (a) polybot 加 batch compute mining 复用; (b) differential test.

## ML PLAN B (如重挖 0 cross-bucket, 启动) — 本轮 199 survivors, 未触发
- LightGBM 找 non-linear interaction → feature importance → 翻译回 rule (`ml_methods.md` §5). rule mining 不退场. 100K+ events 才考虑 MLP/Transformer.

## INFRASTRUCTURE
- **trades async httpx + Clash proxy wedge** — orchestrator 已用 `timeout` 兜底 + trades 排末位; 根治需改 subprocess curl (backfill v3). 同模式适用未来 HTTP IO.

## OPEN QUESTIONS
- Q2. Transform 历史查询跨大 gap 拉 stale data — 加 cs ≥ now-N*300 时间窗?
