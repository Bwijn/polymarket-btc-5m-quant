# Polybot TODO

## P0 — Immediate (今天)

- **KILL R1, R3**: 死因明确 (classifier overfit, bt_wr 远高于 paper_wr, p<0.01). 每天烧 ~$3-5.
  - 写 factor_decisions row (R1: n=88 wr 63.6 vs 77.9; R3: n=45 wr 73.3 vs 92.4)
  - polybot/strategies.py 的 ACTIVE 元组移除 R1, R3 (定义保留作 audit)
  - `bash deploy.sh` 重启

- **H5 → live**: 全 confirmation gate 已过 (drift_t=+0.71, entry_drift=+0.27¢ 最优, wr 65.1% p=0.0001, bt V2_oos nev=+12.19% p=0.054). n=166, 1.1 周 deadline 到.
  - real-money switch 怎么打开 (找 config / wallet 余额校验路径)
  - LIVE_KELLY_FRAC=5% 起 ($1.71/trade @ $34 wallet), 1 周后视情况加到 10%
  - 监控 live 第一周 daily PnL + drift 是否跟 paper 一致

## P1 — Methodology Audit

- **Mining overfit 复盘** (R1/R3 教训): paper wr 比 bt wr 低 14-19pts 且高度显著.
  - clustering / dedup 是否真把 correlated variants 抠干净? (R-series 长得很像)
  - V2 OOS 144 hit / candidate 够 stable 吗? (paper R3 才 45 笔就抓到 wr 崩)
  - mean_ep > 0.7 candidate 是否要单独 derate? (R1/R3/R5 都是高 mean_ep + 失败, R7 mean_ep=0.5 最 robust)
  - 加 `wr_paper_predict` 列到 paper_candidates? 强制 mining 输出"如果 wr 退 10pts 还赚吗"压力测试

- **R5 决策待 align**: 边缘 KILL (EV -3.1% < floor -2.5%), 但 5/17→5/19 转正 (-1.6% → +4.3%). 5/16 大亏可能 outlier. 决策:
  - 选项 A: 按 framework KILL
  - 选项 B: PENDING + watch 1 周 (倾向 B, 等更多 day 数据)

## P2 — Infrastructure

- **R-series pre-reg backfill (Option C, POST-HOC)**: 按 0.5 × bt_v2_nev 公式插 7 行 pre_registrations, notes 标 "POST-HOC, n trades already observed at lock"

- **`polybot/lib/promote_rule.py` SSOT module**: 把 confirmation gate (drift_t<-2 AND mean<-3%, wr_p<0.01 AND gap>10%, EV floor) + decision function 写成单点 SSOT. 加 `rule_module_version` 列到 pre_registrations.

- **Layer 4 code gate**: factor_decisions 写入前强制 JOIN pre_reg_binding 校验阈值一致. 包成 `scratch/tools/decide.py` (或同 module), 禁止裸 INSERT.

- **Cleanup scratch/Hx_*/ stale dirs**: H5 spec 已迁 db, 可删 `H5_sat_dump_cluster_A/`, `H6_*/`. 列其它 Hx_* 看哪些可清.

## P3 — Background (被动等数据)

- R2/R4/R6/R7 继续 paper, 监控:
  - R2 n→40 + 至少 5 真 day → 重判
  - R7 n→30 + 至少 5 真 day → 重判
  - R4/R6 等 n>20

- **下轮 mining**: methodology audit 完成后 (P1) 才启动. 目标补 research 层空缺 (funnel 健康需要 paper queue 15-50 个 alternate, 现在 5-7 个).

## Forward Schema (when next opportunity to add columns)

- `book_depth_at_trigger` (top-of-book size) — 估真 slippage
- `trigger_eval_ms` — 触发计算耗时
- `book_age_at_entry_ms` — 用的 book 离 entry 多远 (stale book bias 检测)
- `fee_rate_at_entry` — PM crypto rate 当时是多少 (PM 改 rate 不静默 invalidate 老数据)

## Open Questions

- **Q1**. H5 alpha 来源识别 (weekend retail / arb gap / thin book / BTC vol regime)?
- **Q2**. Transform 历史查询语义: 当前 SQL = "最近 N 条 cs < current", event-count 跟 mining
      pandas rolling(window=N) 一致, 但跨大 gap 时拉 stale data. 是否加 cs ≥ now-N*300 时间窗约束?

## Misc

book cache
vps traffic problem
