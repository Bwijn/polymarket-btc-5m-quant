# Polybot TODO

## NOW — 没有 proven factor,live 应归零

Phase 1(验证 live 执行链路)完成:drift ~−1%、 redeem 正常。
但**没有 factor 通过 paper→live gate**(gate 定义见 CLAUDE.md Constitution):

- **H5** — KILLED。n=166 paper,+3% net / t=0.44,永不可证。factor_decisions 已记。
- **R2** — paper n=76,+10.78% net / t=1.18,**未达标**。仍 live=True 小 Kelly 跑
  (last 30 EV +0.38%,可能在 decay)。**待决定**:净亏破 -$5 自动降 live=False。
- **R4** — paper-only。

→ 无 proven factor 时,live 正确状态 = 0(不拿真钱赌未证明)。Phase 2 等第一个过 gate 的。

## NEXT — features (trades-derived) → re-mine 是杠杆

> **背景**:trades 4000-cap backfill 2026-05-23 完成,pm_btc5m.db.trades 现 85M rows。
> trade-proxy ≈ 真实 best ask,drift ~1¢ vs 旧 mid (fidelity=1) carry-forward drift ~5-10¢。
> 旧 todo「book recorder」**被本路径取代** — 不再录 forward book depth,直接用 trades
> derive entry price proxy + 8 组 microstructure features。

- **[NEXT-0] coverage 验证** ✅ DONE
  - Level 1: pre_60s 1.4% → 59.2%, missed_180_240 37% → 0.4%, 大洞填满
  - Level 2: et=0 60%, et=60 85%, et=90 92%, et=120+ 96-99% → 决定 grid `[30..270]` 删 et=0/15

- **[NEXT-1] refactor build_features.py → features/ modules** ✅ DONE
  - 拆 625 行单文件 → 7 个 module + 55 行 orchestrator
  - `--source X` 支持 per-source upsert, 可重复
  - test_compute_equivalence 全绿 (PM/Binance/basis byte-identical)

- **[NEXT-2] B 路径: 全 intra cols 切 trade-based** ✅ DONE (10/10 — 2.11 已 defer 到 NEXT-5)
  - [x] 2.1 pm.py 删 INTRA 段输出 (~118 cols 砍)
  - [x] 2.2 mine_gpu.py ENTRY_TIME_GRID 删 0 和 15
  - [x] 2.3 pmtrades.py 接管 entry price family (p_intra_X + delta_intra_X + staleness)
  - [x] 2.4 pmtrades.py 接管 intra window stats (mean/std/rng/max/min, chg_rate)
  - [x] 2.5 pmtrades.py 加 Group 2-8 (flow / impact / velocity / whale / wallets / spread / cross-token)
  - [x] 2.6 strategies.py 审计 — 无 et=15, R4 用 bn 不影响, R2 用 min_intra_90_dn 会切 trade
  - [x] 2.7 compute.py 镜像: 删 _pm_one_side 中 intra + 加 compute_pmtrades_features()
        **架构升级**: features/pmtrades.py 改为 **delegation** → compute.compute_pmtrades_features
        (math SSOT 单一实现, 物理上消灭 drift; pm/bn/basis 仍是 parallel-impl + parity-test 历史包袱)
  - [x] 2.8 test_compute_equivalence 自动适应 (PM 1230 col×evt pairs ↓ from 2410 — intra dropped)
  - [x] 2.9 rebuild features.parquet → shape=(24884, 1598), 200MB (was 1256 / 146MB)
  - [x] 2.10 跑 test_compute_equivalence 全绿 (PM 1230 + BN 140 + basis 60, all 0 diffs)

- **[NEXT-3] re-mine** ← **当前下一步**
  - `uv run python scratch/research/mine_gpu.py --buckets V1 V2` (~5-7h GPU 2060)
  - 新 features.parquet 输入 (1598 cols, 200MB; 206 pmt_* + 127 pm_* + 14 bn_* + 6 basis + 7 futures + 1240 transforms)
  - 关键改动 vs 上轮: ENTRY_TIME_GRID 删 0+15 → grid `[30..270]`,mine_gpu.py 已同步
  - 预期: ~50K Phase A candidates → top 50K seed → Phase B 全 brute → survivors_v1/v2.parquet

- **[NEXT-4] paper funnel 拓宽** — 旧目标 3-4 → 15-50。re-mine 产出 top 20-30 入 paper。
  - **deploy 注意**: bot VPS 现跑旧 code,R2 用旧 mid-based min_intra_90_dn 仍正常 paper
  - 新 candidates 若用 pmt_*/p_intra_*/mean_intra_* 等 trade-based col → **不能 deploy 直到 NEXT-5 完成**
  - 若全用 pm_pre_*/bn_*/basis_*/futures_*/time → 可直接 deploy

- **[NEXT-5] scanner runtime 加 trades fetch** — 解锁 pmt_* 因子 live deploy
  - compute_pmtrades_features() 已加(NEXT-2.7), 现在 scanner runtime 没 trades 数据喂它
  - 选项 A: PM WS subscribe market.trades channel (live trades stream, 复用 book conn)
  - 选项 B: REST poll /trades 在 active candle 触发时 (低频, 简单)
  - 触发条件: ACTIVE 中第一个引用 pmt_* / p_intra_X / mean_intra_X 等 col 的 factor
  - R2 当前 expr 用 min_intra_90_dn → 一旦 NEXT-2 deploy, R2 paper 暂时挂(scanner 算不出)
    → 等 NEXT-5 解锁

- **[NEXT-6] gate-check 代码化** — 每 factor 算 n/EV/std/t,checkpoint 自动判
  GRADUATE/KILL。现在是手算。

- **[NEXT-7] compute.py 拆 module + 全 family delegation 化** — 739 行单文件臃肿
  - 拆 `polybot/lib/compute.py` → `polybot/lib/compute/` package:
    - `__init__.py` (re-export 保 backward compat)
    - `_helpers.py` (forward_fill_grid, stats_window)
    - `pm.py` (compute_pm_features, _pm_one_side)
    - `binance.py` (compute_bn_features)
    - `basis.py` (compute_basis_features)
    - `pmtrades.py` (compute_pmtrades_features + _pmt_* helpers + constants)
    - `transforms.py` (compute_zs, compute_rank, parse_transform_col)
  - **同步消化 Phase 2 包袱**: 把 features/pm.py + features/binance.py + features/basis.py
    都改成 delegation → polybot.lib.compute (像 features/pmtrades.py 现在的样子)
  - 完成后: math 全程 SSOT,test_compute_equivalence 可大幅瘦身(pm/bn/basis 删,留 transforms)
  - SSOT 风险: 仅 import path 变化, math 0 改动; test 仍守门 transforms
  - 触发: NEXT-3 (re-mine) 完成有空间再做, 不阻塞 mining

## DECISIONS WAITING

- **R4** — n=26,并入新 gate(CLAUDE.md Constitution):持续 paper 直到 t>1.65
  graduate 或到 cap 被 kill。旧「n=50 / netEV≥+0.03」判据作废(underpowered)。

## ML PLAN B(仅当  features re-mine 后 graduating 仍 ≤1 时启动)

- **LightGBM 找 non-linear interaction** → feature importance → 翻译回 rule 验证
  (`ml_methods.md` §5)
- rule mining 永远不退场(`ml_methods.md` L283),LightGBM 只做 feature discovery
- 数据扩到 100K+ events 后才考虑 MLP / Transformer(目前 24K 太少)

## INFRASTRUCTURE

- **trades pm_btc5m.db 现 61GB** — VS Code Database Client extension 设
  `database-client.autoGetTableCount: false` 已解决 open table 卡死。
- **httpx async + Clash proxy 长连接 wedge** — backfill v3 改用 subprocess curl
  绕开,稳跑 ~3h 0 永久失败。未来 mining 阶段如有 HTTP IO 同样模式应用。
- **Cleanup scratch/Hx_*/**:H5/H6 已 kill、spec 已迁 db,删。
- **旧 VPS(荃湾)~05-31 到期**:cold standby fallback;新机稳定后不续费。

## OPEN QUESTIONS

- Q2. Transform 历史查询:跨大 gap 拉 stale data,加 cs ≥ now-N*300 时间窗?
