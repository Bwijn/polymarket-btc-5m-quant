# Operating Plan — $34 → $10k Personal Contract

**Locked**: 2026-05-19. **Subject**: 自己. **Override 规则**: 改动必须 git commit, 写明改动理由 + 当时心理状态.

## 目标 + 数学

```
$34 → $10,000   (294x 增长)
分阶段路径 ≈ 2-3.5 年, 取决于 alpha decay + capital injection 时机
```

复利公式: `V_n = V_0 × (1 + r)^n`
Kelly 增长: `G = p·log(1+f·b) + (1-p)·log(1-f)`
H5 最优 Kelly = **5.5%**, 当前 LIVE_KELLY_FRAC=5% 已贴最优.

**Leverage 死定**: f > Kelly 最优 → variance drag → G 下降; f > 2× Kelly → G 归零; f > 3× Kelly → 数学保证爆仓.

## 4 Phase Gate

### Phase 1 — PROOF ($34 → $100, 2-3 月)
- Live: **只 H5**
- Kelly: 5%
- 加 capital: ❌ 禁止
- 提取: ❌ 禁止
- 进 Phase 2 gate (全 4 条必须满足):
  1. 累计 wallet ≥ $100
  2. Live 至少 4 周
  3. 周线 drift_t 持续 > -2
  4. Live wr 跟 paper wr 差 < 5pts
- 失败 (wallet < $20): STOP, 不补 capital, 复盘 system

### Phase 2 — VALIDATION ($100 → $500, 3-4 月)
- Live: H5 + 1 个 R-series (R2 或 R7 先 promote 的)
- Kelly: 5% per strategy
- 加 capital: ✓ 一次 ≤ $500 (wallet 到 $200 + Phase 1 gate 全过后)
- 提取: ❌ 禁止
- 进 Phase 3 gate:
  1. 累计 ≥ $500
  2. ≥ 2 个 live factor
  3. 至少 1 个跨 regime (BTC ±15% 那种) 没爆仓
- 失败 (wallet < $80): 砍到 1 strategy, 不补 capital

### Phase 3 — SCALING ($500 → $2,000, 4-6 月)
- Live: 3-5 个 (paper queue 晋升)
- Kelly: 组合总不超 15-20% (低相关 strategy 分担)
- 加 capital: ✓ $500-1000, 每 3 月最多一次
- 提取: ⚠ 允许 10% self-bonus (心理 reward, 防 burnout)
- 进 Phase 4 gate:
  1. 累计 ≥ $2,000
  2. ≥ 3 个 live + paper queue ≥ 5 alternate
  3. 新 mining cycle 至少 1 轮跑完, 不再产 R1/R3 同款 overfit
- 失败 (任一 strategy MDD > 50%): pause 该 strategy, 其它继续

### Phase 4 — MATURITY ($2k → $10k, 6-12 月)
- Live: 5-10 个
- Kelly 总: 20-25%
- 加 capital: 看 IRR vs alternative 投资再定
- 提取: 20-30% 当 living expense (旅居开始 partial 实现)

## 日常 Rhythm — 防浮躁纪律

```
DAILY (5 min):  看 drift_t 一个数. 没炸 → 关电脑 / 出门
WEEKLY (30 min): drift_t / wr_p / 周净 PnL / MDD 四个数 + 1 行 journal
MONTHLY (2 h):   全 strategy gate 重判 + paper queue 谁可 promote + 月报
QUARTERLY (1 d): mining methodology audit + 新 mining cycle + Phase gate 判
```

**纪律**: 跳 cadence = 浮躁第一表现. 治法 = 强制 cadence + 物理隔离 (账户密码存 password manager, 不背).

## 死线 (任一触发 = STOP, 不 negotiate)

| 触发 | 动作 |
|---|---|
| 单 strategy MDD > 50% | pause 该 strategy, factor_decisions 写一行 |
| 全 portfolio MDD > 30% | pause 全部 live, 1 周 cool-down |
| 连续 3 周 net 负 | pause 全部, 强制 audit |
| 单次 wallet 减少超过心理承受值 | pause 全部, sleep on it 24h |

**心理承受值** ≠ 财务承受值. 看到 -$X 心跳到 100 = 当前 emotional risk cap = 把 size 降到不触发它的水平, 不要"克服".

## 5 条 Personal Commitment

```
1. 不在 Phase 1 加 capital (无论看着多想)
2. 不在任何 phase 加 leverage 超 Kelly 最优
3. Phase gate 没过, 不进下一 phase (即使别人在赚)
4. 死线触发立刻 pause, 不是 "再看看"
5. 至少每月 1 次去做跟 quant 无关的事 (骑车 / 旅行 / 学新东西), 防 over-identification
```

## $10k 后 ≠ 财务自由

Bangkok 月开销 $1k, 摩托车 $3-5k. $10k = system 已 verified 24x 资本, 不是终点.

下一步选项 (留给未来的自己):
- 继续 scale 到 $50k-100k (再 12-18 月)
- 复制 system 到更大资本 (LP / day job seed)
- System 本身作为 IP 输出

## Mantra

> Phase gate 没过不进下一 phase, 单一 metric (drift_t) 没炸不焦虑, daily-noise 不打开账户.
> 复利的速度是数学常量, 我只控制纪律是不是常量.

---

**Override 触发条件**: 修改本文档前回答 — "如果当前是浮躁版本的我, 这个改动会让我死得更快吗?" 答 yes → 不改.
