# Polybot TODO

## NOW — 没有 proven factor,live 应归零

Phase 1(验证 live 执行链路)完成:drift ~−1%、Component 6 redeem 正常。
但**没有 factor 通过 paper→live gate**(gate 定义见 CLAUDE.md Constitution):

- **H5** — KILLED。n=166 paper,+3% net / t=0.44,永不可证。factor_decisions 已记。
- **R2** — paper n=69,+11% net / t=1.12,**未达标**。仍 live=True 且实亏(live
  net −$0.52)。**待决定**:降 `live=False` + 挂 pre-reg forward window(~80 单见分晓)。
- **R4** — paper-only。

→ 无 proven factor 时,live 正确状态 = 0(不拿真钱赌未证明)。Phase 2 等第一个过 gate 的。

## NEXT — 拓宽 funnel 是通往「第一个 proven factor」的真杠杆

- **拓宽 paper funnel**:现 3-4 候选 → 目标 15-50 并行。bt 只粗筛,paper 选拔。
  (不是松 gate —— 松 gate 只会更快上一个亏钱的。)
- **gate-check 代码化 (SSOT)**:每 factor 算 n/EV/std/t,checkpoint 自动判
  GRADUATE/KILL。现在是手算。
- **book recorder**:carry-forward 的 forward fix —— 录每根 candle 各 entry
  offset 真实 ask + depth(估真 slippage)。下轮 mining 前做。

## DECISIONS WAITING

- **R4** — n=26,并入新 gate(CLAUDE.md Constitution):持续 paper 直到 t>1.65
  graduate 或到 cap 被 kill。旧「n=50 / netEV≥+0.03」判据作废(underpowered)。

## INFRASTRUCTURE

- **Cleanup scratch/Hx_*/**:H5/H6 已 kill、spec 已迁 db,删。
- **旧 VPS(荃湾)~05-31 到期**:cold standby fallback;新机稳定后不续费。

## OPEN QUESTIONS

- Q2. Transform 历史查询:跨大 gap 拉 stale data,加 cs ≥ now-N*300 时间窗?
