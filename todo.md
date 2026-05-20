# Polybot TODO

## NOW — Build Live Execution Layer

当前系统是纯 paper simulator, 零真钱下单代码 (config.py:1 "Live config loaded later").
"上 live" = build 一个子系统, 不是翻开关.

- **先 align 设计决策** (动手前):
  - private key 怎么管 (安全 — 错了直接丢钱)
  - 官方 SDK: py-clob-client-v2 (CLAUDE.md 强制官方)
  - 第一笔 live 最小化测试 ($1 PM min 验证全链路再上正常 size)

- **Build 8 组件**:
  1. 装 py-clob-client-v2
  2. Credentials 加载 (private key / API key / funder address)
  3. 下单: HIT → 签名 + 提交 CLOB taker BUY
  4. Fill 处理: 成交确认 + 实际成交价 vs book_ask (真 drift)
  5. Position 跟踪 (真实持仓, 不只 db row)
  6. Redeem: market resolve 后领奖
  7. Wallet 余额查询 (给 Kelly sizing)
  8. Error handling (下单失败 / 网络 / 余额不足 / 被拒)

- **Sizing 改造** (live 时): KELLY_FRAC config (H5/R2=0.05) + `wallet × kelly_frac` 动态 size, 取代 PAPER_SIZE_USD 固定值

## NEXT (Live layer 建好 + Phase 1 验证通过后)

- Phase 1: H5+R2 live on $34, 各 5% Kelly, 1 weekend+weekday 验证 drift/fee/settle
- Phase 2: 充值 $100 → $134, sizing 自动 4x scale

## DECISIONS WAITING

- **R7**: EV +20% 漂亮但 drift_t=-2.18 d_mean=-11% (drift 警报, leading indicator). paper-only watch, n→30 后: drift 持续 → KILL, drift 回正 → 考虑 live

## INFRASTRUCTURE (可跟 live 并行)

- **Mining overfit 复盘** (R1/R3 教训): cluster dedup 漏洞 + OOS 144 hit 不稳 + mean_ep>0.7 derate + 加 `wr_paper_predict` 压测列
- **promote_rule.py SSOT**: confirmation gate 函数化 (drift_t<-2 AND mean<-3% / wr_p<0.01 AND gap>10% / EV floor)
- **R-series pre-reg POST-HOC backfill**: H5/R2/R7 入 pre_registrations 表 (notes 标 POST-HOC)
- **Layer 4 gate**: factor_decisions 写入前 JOIN pre_reg_binding 校验
- **Cleanup scratch/Hx_*/**: H5/H6 spec 已迁 db, 删

## BACKGROUND (被动等数据)

- R4/R5/R6 paper-only 继续, n>20 才有判断价值
- 下轮 mining (methodology audit 后启动): 补 research 层空缺 (paper queue 健康需 15-50 alternate, 现 5-7)

## FORWARD SCHEMA (下次加列机会)

- book_depth_at_trigger (top-of-book size, 估真 slippage)
- trigger_eval_ms (触发计算耗时)
- book_age_at_entry_ms (用的 book 离 entry 多远, stale book bias 检测)
- fee_rate_at_entry (PM crypto rate 当时是多少, PM 改 rate 不静默 invalidate 老数据)

## OPEN QUESTIONS

- Q1. H5 alpha 来源识别 (weekend retail / arb gap / thin book / BTC vol regime)?
- Q2. Transform 历史查询: 跨大 gap 拉 stale data, 加 cs ≥ now-N*300 时间窗?
