# Polybot TODO

## NOW — testing 基建 + hypothesis 列弃用 (expr-only SSOT) + 5 factor 进 paper + R4 graduate

bot 涉资金, MVP 阶段 testing/migration 都 ad-hoc, 本批补齐. **minimum scope, 一个一个做, 每项 review.**

- [x] **N1 — pytest + integration smoke test** — `tests/` (repo 根, 不 deploy), 5 passed. fixture=录制真 candle (smoke_candle.json). no-fund-touch (LIVE 强制 False + 断言 sc.live is None).
- [x] **N2 — migration runner + `001_drop_hypothesis`** — `migrations/` (repo 根, 不 deploy; N6 scp 到 VPS). up/down/幂等/数据保留 验过. VPS sqlite 3.40.1 ≥3.35 ✓.
- [x] **N3 — 代码改动 (5 文件)** — strategies `id→label` · scanner dedup/seed→`expr`+删 hypothesis 写入+log 用 label · models 删 hypothesis 字段 · verify_active_audit · compute_drift(+1, dev 脚本). 内联验证 post-N3 路径 OK.
- [x] **N3b — config hygiene** — `KELLY_FRAC→BANKROLL_FRAC` (改名消歧: fixed-fraction 非 Kelly) · 清 dead H5/R2 键 · 加 `"R4":0.05` (值写好, **R4.live 仍 False, 未 arm**).
- [x] **N4 — smoke test 翻 post-migration schema** — pytest 5 绿 (含 "无 hypothesis 列" 断言).
- [x] **N5 — dry-run 拿生产副本空跑** — 1233 行副本 migrate up 干净 (hypothesis 删/行保留/无 view-index 阻塞), post-migration 代码读副本 OK (按 expr 认出 R4).
- [x] **N6 — live 下单 dry-run test** — `tests/test_live_dry.py` 6 绿 (订单 token/size/price + FOK SDK args + zero-fill 判败). 全 suite **11 绿**.
- [x] **N7 — arm R4 + stop-the-world deploy (2026-06-02)** — R4.live=True; VPS stop→备份→migrate up (hypothesis 删, 1233 行保留)→rsync→uv sync→start. 启动干净: 6 strategies, live executor ready (signer 0xbd27…), 无 ERROR. 本地 mirror 已 sync. deploy.sh 加 pytest 第4闸门. (停机~2h 含 interrupt, paper gap 无损.)

**🔭 观察中 (DEPLOYED, 真金 live)**: R4 armed @ 5% bankroll ($36 钱包→~$1.80/笔, >$1 floor). 等首笔 live order: `ssh vps "grep 'LIVE BUY' /opt/polybot/polybot.log"`. 硬急停 = `ssh vps systemctl stop polybot`. 5 paper factor 同时开跑, 等积累 paper EV.

5 factor + R4 已入 ACTIVE (=6), 6 条过 validate. 看板: `SELECT * FROM paper_candidates WHERE cycle_tag='per_dollar_20260602'` (pm_btc5m.db); 实盘 `SELECT * FROM paper_active_agg` (polybot_live.db).

**已决/背景** (别重 walk):
- **命名/身份 SSOT**: `expr` = 唯一 identity (dedup key + db join key). `label` (机制简写) 只当 log 装饰, **永不入库当 key**. 弃 R/H 顺序编号. 类比 git: expr=commit hash(身份), label=branch名(别名). hypothesis 列 drop (migration 001).
- **sizing**: `BANKROLL_FRAC` = 押本金 (bankroll) 固定 % (不看 edge), **NOT Kelly** — 改名消 KELLY_FRAC 歧义 (旧名骗人, 代码做 fixed-fraction). R4 full-Kelly≈26%, **5%≈0.2× = 保守** (应对 marginal CI + 首次真金 + live 代码没跑过). fractional-Kelly (λ·f\*, λ=0.25–0.5) = 未来升级, 待真金 OOS 收窄 edge 估计.
- **config-as-code**: 逻辑+arming+sizing 留 code (git 审计 + deploy test 闸门), **不 refactor 成 DB-driven** (会丢审计+闸门, 对动钱旋钮是降级非升级). 真机构: logic→code+review+CI, 仅急停/高频操作→可轮询 store. 硬急停 = `systemctl stop` (已 deploy-free). 细粒度"停 live 保 paper" = 可选缓 (见 INFRASTRUCTURE).
- **testing 策略**: test pyramid — unit(纯数学) / integration(Scanner+tmp db+录制 fixture, =smoke test) / contract(只读真 API + live 下单 dry-run). 铁律: ①测试不可碰资金 (LIVE=False + 真 key guard); ②fixture=录制真 response; ③main.py 跑=部署后观察, 非正确性闸门. contract/live-dry = N6 (arm R4 前置).
- **migration 策略**: 单 VPS 单进程可短暂停服 → stop-the-world (停服迁移), 不用 expand/contract (零停机, 多实例才需). 必须: versioned 脚本 + 迁移前备份 + 拿 prod 副本 dry-run.
- **R4 graduate 数据 (synced 2026-06-02)**: n=131 settled, winrate 64.9%, net EV **+20.7%** (std 94%), **t=2.45** (过 gate), 95% CI **[+4.1%, +37.2%]** (下界擦 5% hurdle), MDD -$9.27, last40 +30.8% (近端更强无 decay). → GRADUATE 成立; 5% 小额上线应对 marginal CI.
- **dedup 方法固化**: gates.py `FACTOR_DEDUP_OVERLAP_MAX=0.45` + `CAPEFF_TIEBREAK=0.15`; `scratch/research/dedup_survivors.py`. 唯一 filter=去 correlation, 不设数量 cap.
- **R2/P1-P4 = KILLED** (per-$1 OOS+paper 双弱); **H5 = KILLED** (legacy 反面教材: 凭感觉 live, 违 gate).
- 看板: sync 后 `SELECT * FROM paper_active_agg` (polybot_live.db) = ACTIVE factor 实盘.

## NEXT (顺序)
- **[NEXT-1] transforms.py SSOT-ify** — mining pandas vs polybot per-event 双实现. 不阻塞 (byte-equal, verify_compute_ssot cover). 选项: (a) polybot batch compute 复用; (b) differential test.

## ML PLAN B (如重挖 0 cross-bucket, 启动) — 本轮 199 survivors, 未触发
- LightGBM 找 non-linear interaction → feature importance → 翻译回 rule (`ml_methods.md` §5). 100K+ events 才考虑 MLP/Transformer.

## INFRASTRUCTURE
- **trades async httpx + Clash proxy wedge** — orchestrator 已 `timeout` 兜底 + trades 排末位; 根治需 subprocess curl (backfill v3).
- **LIVE_ENABLED=True = armed-but-idle** — 至今无 strat live=True, LiveExecutor 实例化但无人触发; R4 arm (N7) 后才真动钱. 本地跑 main.py 会读 PRIVATE_KEY → 必须 LIVE=False (no-fund-touch 铁律).
- **(可选, 缓) deploy-free "停 live 保 paper" 轮询开关** — scanner 每 loop 读 flag (文件/env/runtime_config 表), 只能停 live 不能 arm. systemctl stop 已兜底硬急停, 故非紧急.
- **(未来) fractional-Kelly sizing** — 待真金 OOS 收窄 edge, BANKROLL_FRAC 升级为 λ·f\* (按 w/ep 算 per-strategy Kelly, λ=0.25–0.5).

## OPEN QUESTIONS
- Q2. Transform 历史查询跨大 gap 拉 stale data — 加 cs ≥ now-N*300 时间窗?
