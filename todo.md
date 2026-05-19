# Polybot TODO

## NOW

- **KILL R1, R3** (overfit: paper wr 14-19pts < bt wr, p<0.01)
  - factor_decisions row × 2
  - strategies.py: ACTIVE = (H5, R2, R7) live + (R5, R6, R4) paper-only (加 per-strategy `live` flag)
  - bash deploy.sh

- **Live Phase 1 启动** ($34 wallet, 3 strategy each 5% Kelly = $1.70/trade)
  - 加 KELLY_FRAC config: H5/R2/R7 = 0.05 (R2 ≈ half Kelly of f*≈9%, R7 ≈ quarter Kelly of f*≈24%)
  - sizing 改成 `wallet × kelly_frac` 一行
  - real-money switch 路径找出来打开 (config / wallet 校验)
  - 监控 1 weekend + 1 weekday: drift_t / wr / fee / settle 是否正常

## NEXT (Phase 1 通过后)

- 充值 $100 → $134, sizing 自动 4x scale ($6.70/trade)
- 继续 monitor 进 Phase 2

## DECISIONS WAITING

- **R5**: 边缘 KILL (EV -3.1% < floor -2.5%), 但 5/17→5/19 转正 (-1.6% → +4.3%). 5/16 大亏可能 outlier. 倾向 PENDING + watch 1 周 (live flag=False, 仅 paper)

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
