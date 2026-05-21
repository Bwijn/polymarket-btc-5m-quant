# Polybot TODO

## NOW — Phase 1 live validation

Live ARMED 2026-05-20 (LIVE_ENABLED=True, 新 VPS 四区). paper_trade_5m_binary
3 线 (bt/paper/live). H5/R2 触发即下真 FOK 单, size = wallet × 5% Kelly.
首批 live: 2 单 R2 全胜 (#629 +$0.35 / #644 +$3.05, computed net +$3.40),
1 单 FOK 杀单 (#636, slippage cap 拦, 无损).

- **手动 redeem** — 6.8 份 winning tokens 锁在 proxy wallet, 现金 ~$30.7、~6 天 runway. redeem 时记 before/after USDC → ground-truth 对账 `pnl_usd_live`
- 已验证: fee 公式实测精确吻合 ✓; 执行 drift 已量化 (et>0s ~0, et=0s ~−7%)
- 攒够 live n → R2 live drift 是否吃掉 net EV → 定 Phase 2. **R2 未证: n=49 paper + n=2 live**

## NEXT

- **Component 6 — 程序化 redeem**: 走 Relayer API (`relayer-v2.polymarket.com/submit`, gasless meta-tx) 签 redeem, 优于裸 web3 (免 gas, 配 proxy 账户). CLOB SDK 无此功能. **无人值守 live 前必做**
- Phase 2: 充值 $100 → ~$134, dynamic sizing 自动 scale

## DECISIONS WAITING

- **R4 grace (pre-registered kill)**: R4 现 n=26, net EV +1.6% 但 gross EV t=+0.27 (零 edge, 正 net 是噪声). grace 到 **n=50 复查** — KILL, 除非 net EV ≥ +0.03 (清掉 fee 仍有余、证明真 edge). 默认 KILL, 心理预期 kill. (R6 已 kill 2026-05-21, factor_decisions id=6)

## INFRASTRUCTURE (可跟 live 并行)

- **Mining 方法论修复** (root cause 已定位 = carry-forward, 见 CLAUDE.md Trading Constitution 第3条): 7 candidate 只 R2 活. 修法 — ① bt 降级 coarse pre-filter, 永不信其 EV 数值; ② paper = 唯一真 OOS; ③ et=0s 候选扣 ~−7% haircut, 优先 et>0s; ④ 拓宽 paper funnel (别信 bt top-N)
- **book recorder (新)**: carry-forward 的 forward fix — 现 WS 只 trigger 时取瞬时 ask、不持久化. 需建 recorder 录每根 candle 各 entry offset 真实 ask → 攒真实 book 数据集. 下轮 mining 前做- **Cleanup scratch/Hx_*/**: H5/H6 spec 已迁 db, 删
- **旧 VPS (荃湾) ~05-31 到期**: cold standby fallback; 新机稳定确认后不续费

## BACKGROUND (被动等数据)

- 下轮 mining: carry-forward 修法 (①-④ + book recorder) 落地后启动; paper queue 健康需 15-50 alternate, 现 3-4

## FORWARD SCHEMA (下次加列机会)

- book_depth_at_trigger (top-of-book size, 估真 slippage)
- trigger_eval_ms (触发计算耗时)
- book_age_at_entry_ms (用的 book 离 entry 多远, stale book bias 检测)
- fee_rate_at_entry (PM crypto rate 当时是多少, 现实测 0.07; PM 改 rate 不静默 invalidate 老数据)
- pnl_usd_live_realized (redeem 时实测到账额, 跟计算 pnl_usd_live 对账 — 跟 Component 6 一起加)

## OPEN QUESTIONS

- Q1. H5 alpha 来源识别 (weekend retail / arb gap / thin book / BTC vol regime)?
- Q2. Transform 历史查询: 跨大 gap 拉 stale data, 加 cs ≥ now-N*300 时间窗?
