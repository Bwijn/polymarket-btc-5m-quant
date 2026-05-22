# Polybot TODO

## NOW — Phase 1 live validation

Live armed (LIVE_ENABLED=True, 新 VPS). paper_trade_5m_binary 3 线
(bt/paper/live). H5/R2 触发即下真 FOK 单, size = wallet × 5% Kelly.
Component 6 自动 redeem 已上线 (30-min in-process sweep, 胜单 → pUSD).

- **H5 明天正式 trigger** — 首次 live 触发. 参考 R2: paper→live 执行
  drift 小 (~−1%, 可控), bt EV 不可信 (carry-forward 失真, 见 Constitution)
- **R2 攒 live n 判真 EV** — 现 n=17 live: audit 已对账 table ↔ 链上
  100% 一致, 真实 net +$1.97 但 **outlier-dependent** (#644 单笔 +3.05,
  其余 16 单合计 −1.08). 判 R2 一律用 pnl_usd_live_net, 禁 gross
- Phase 2: 充值 $100 → ~$134, dynamic sizing 自动 scale

## NEXT

- **DB schema 重构** (draft: `scratch/models_v2_draft.py` — 13 改名 + 按线
  重排序): 命名统一标注线 (bt/paper/live). 当前 schema 够用, **不舒服再
  做** —— 表 rebuild 需 bot 停机窗口

## DECISIONS WAITING

- **R4 grace (pre-registered kill)**: R4 现 n=26, net EV +1.6% 但 gross EV
  t=+0.27 (零 edge, 正 net 是噪声). grace 到 **n=50 复查** — KILL, 除非
  net EV ≥ +0.03 (清掉 fee 仍有余、证明真 edge). 默认 KILL, 心理预期 kill

## INFRASTRUCTURE (可跟 live 并行)

- **Mining 方法论修复** (root cause = carry-forward, 见 CLAUDE.md Trading
  Constitution 第3条): ① bt 降级 coarse pre-filter, 永不信其 EV 数值;
  ② paper = 唯一真 OOS; ③ et=0s 候选扣 ~−7% haircut, 优先 et>0s;
  ④ 拓宽 paper funnel (别信 bt top-N)
- **book recorder (新)**: carry-forward 的 forward fix — 录每根 candle 各
  entry offset 真实 ask + depth (top-of-book size, 估真 slippage) → 攒真实
  book 数据集. 下轮 mining 前做
- **Cleanup scratch/Hx_*/**: H5/H6 spec 已迁 db, 删
- **旧 VPS (荃湾) ~05-31 到期**: cold standby fallback; 新机稳定后不续费

## BACKGROUND (被动等数据)

- 下轮 mining: carry-forward 修法 (①-④ + book recorder) 落地后启动;
  paper queue 健康需 15-50 alternate, 现 3-4

## OPEN QUESTIONS

- Q1. H5 alpha 来源识别 (weekend retail / arb gap / thin book / BTC vol regime)?
- Q2. Transform 历史查询: 跨大 gap 拉 stale data, 加 cs ≥ now-N*300 时间窗?
