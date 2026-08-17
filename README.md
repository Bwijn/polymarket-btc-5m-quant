# polymarket-btc-5m-quant

Polymarket BTC 5 分钟二元市场的完整量化系统 —— 因子挖掘、特征工程、paper 验证、实盘执行。
**真实跑在生产环境上,用真金白银试错过。现已全部公开,包括数据和因子。**

An end-to-end quant stack for Polymarket's BTC 5-minute binary markets. Production-deployed,
traded with real money, now fully open-sourced — code, data, and factors included.

---

## 这不是 vibe coding 出来的东西

半年时间,**每一处代码都经过人工逐行 review**,不是让模型生成完就丢上去跑。

它真实进过生产:

- systemd 常驻在 VPS 上,websocket 订阅盘口,自动下单、结算、redeem (赎回)
- **615 笔真金白银的成交**(早期 copy-trade 298 笔净盈利,5m binary 实盘 317 笔)
- 3,560 笔样本外 paper 单,横跨 3.5 个月
- 42,224 个市场的完整盘中价格轨迹,292 个工程特征

每个数字都能在公开的数据库里用一行 SQL 查出来。

---

## 数据里留着还没被吃掉的东西 —— 先到先得

`polybot_live.db` 完全公开,里面的 `factor_roster` 有 43 个因子和它们的完整状态。
其中**仍在架、且样本外净 EV 为正**的:

| label | n | net EV (扣费后) |
|---|---|---|
| `chgdn900_zs7d_dn` | 56 | **+12.83%** |
| `chgdn300_zs24h_up` | 44 | **+10.45%** |
| `R4` (`bn_taker_buy_ratio_pre_300>0.755`) | 569 | **+1.58%** |
| `bn_chg3600_rank_up` | 224 | +0.88% |
| `bn_tbr900_rank_dn` | 180 | +0.68% |

R4 是样本最厚的一个 —— 569 笔样本外交易,扣掉 7% 手续费之后仍然为正。它后期 decay (衰减)
得比较厉害,这也是我停手的原因之一,但**信号本身在数据里是真实存在的**。

前两个净 EV 双位数,只是样本还薄(n=56 / n=44),没跑到能下结论的量。**它们现在是公开的,
谁接手谁验证。**

> 这些不是回测出来的曲线,是逐笔记录的样本外 paper 成交,每一笔都有 entry price (入场价)、
> 盘口快照、结算结果。`paper_trade_5m_binary` 表里 3,560 行,自己去查。

---

## 解决了哪些坑

如果你在做同类项目,下面每一条都是我用时间或钱换来的。

### PM `/trades` 有 4000 条硬上限,而且**静默截断**

早期 ingest 用 `limit=500` × 3 个 offset,以为拿全了。实测探到真实边界:`limit≤1000` 是
**静默生效**的(传 5000 不报错,只给 1000),`offset≤3000` 强制 —— 单个 cid 最多 4000 条。

后果:**98.6% 的市场早盘成交没抓到**,回测的 entry price 估计整体有偏。

修复见 [`ingest_pm_trades_v3_4kcap_backfill_20260523.py`](research/ingestion/ingest_pm_trades_v3_4kcap_backfill_20260523.py)。
**分页参数必须 probe (探测) 真实边界 —— 不能信文档,也不能信"没报错"。**

### 限速凭印象猜 = 白扔容量

没查文档,凭感觉限到 1.3 req/s。实测 data-api 是 **200 req/10s**,CLOB `/prices-history`
是 **1000 req/10s** —— 只用了 0.13% 的配额,ingest 慢了两个数量级。

官方文档有 `llms.txt` 索引,rate-limits 是独立一页。**用了 SDK 也一样,限速是 endpoint 的
属性,跟客户端无关。**

### 文档写的 enum 和真实返回不一样

文档说 `GET /data/order/{id}` 返回 `status: "ORDER_STATUS_MATCHED"`,实际返回 `"MATCHED"`,
没有前缀。结果 11 笔已成交的单子永远卡在 pending,$11 锁在托管里,钱包被饿死。

**涉及资金的 enum,以实际录制的 response 为准。docs 不能跳过 probe,probe 也不能跳过 docs。**

### `success: true` 不等于成交

FOK (Fill-Or-Kill) 订单会返回 `success: true` + `status: "delayed"` —— 意思是**钱已经出去了**,
只是异步撮合。只在 `matched` 时记账,这笔钱就掉到账本外面了。

**任何 `success: true` 都意味着钱可能已经花掉,必须落库。**

### 先记账,再执行

上面几个 bug 的共同根因是同一个:假设了外部 API 的行为却没验证过。架构上把顺序焊死:

1. 先写 DB 记录意图(`status='pending'`)
2. 再调外部 API
3. 按结果更新
4. 异常时清理预留

进程中途崩溃,去重和状态追踪也不会有盲区。**真金白银的代码要对「未知状态」安全,不只是
对「已知状态」安全 —— mock 测试只能验证你已经知道的逻辑。**

### 不装第三方 wrapper SDK

只用官方 SDK,公开只读 endpoint 用 `httpx` 直调。**不装任何"便利封装"**,star 再多也不装。

X 和 Reddit 上已有多起报告:第三方 Polymarket 封装包藏后门、偷私钥、悄悄改订单参数。
下单 / 撤单 / 提现这类写操作,只走官方 SDK。

### 因子准入:默认拒绝,举证责任在因子

阈值全部集中在 [`polybot/lib/gates.py`](polybot/lib/gates.py),全局统一,**不给单个因子调参**:

| Gate | 值 | 含义 |
|---|---|---|
| `PAPER_TO_LIVE_NET_EV` | 0.05 | 净 EV 点估计要过 5% |
| `PAPER_TO_LIVE_T_STAT` | 1.65 | 净 EV 的 t 统计量 |
| `PAPER_TO_LIVE_CAP_N` | 800 | 到 800 单还没毕业就杀 |
| `PAPER_TO_LIVE_CAP_WEEKS` | 10 | 挂钟超时也杀 |
| `FACTOR_DEDUP_JACCARD_MAX` | 0.75 | 与已上线因子重叠过高则拒绝 |

43 个因子里杀掉 27 个,就是这么杀的。**在可行样本量内证不出来的 edge,就算是真的也要杀** ——
太慢的优势在 7% 手续费面前不值钱。

---

## 架构

```
polybot/              生产 bot(VPS + systemd 常驻)
  lib/gates.py        ← 所有阈值的 SSOT。从这里开始读
  lib/expr_eval_v1.py 因子表达式求值器 —— 因子是数据不是代码,加因子不改程序
  lib/compute/        研究与实盘共用的计算逻辑,保证两边算出同一个特征
  runtime/            scanner、PM/Binance 客户端、websocket、下单执行、redeem
research/
  features/           特征工程流水线 → features.parquet
  ingestion/          历史数据 ingest(Binance K 线、PM trades)
  mine/metrics.py     所有 scorecard 共用的统计口径
docs/                 方法论笔记
tests/                含一个基于真实录制 response 的 smoke test
```

## 为什么停

7% 的 taker fee (吃单费) 是全平台最高档。在这个成本结构下,想靠高频小 edge 复利,需要单笔
成本趋近于零和千万级交易量 —— 对小资金是结构性不成立的。R4 后期 decay 加剧之后,我决定收手。

**但代码、数据和因子都留在这里。** 手续费结构会变,场子会变,接手的人条件不一定和我一样。

---

## 数据

体积原因不放仓库,单独分发:

| 数据集 | 内容 |
|---|---|
| `pm_btc5m.db` | 42,224 个市场、`ep_panel`(40 列盘中价格)、215,388 根 Binance K 线、资金费率 / 持仓量 / 多空比 |
| `features.parquet` | 42,224 行 × 292 个特征 |
| `polybot_live.db` | `factor_roster`(43 因子 + status)、`factor_log`(每个因子被杀的理由)、3,560 笔 paper 成交明细 |

**`ep_panel` 拿不到第二次** —— 它是从 PM `/trades` 的回溯窗口里算出来的,那个窗口已经关了。
这 42,224 个市场的盘中价格轨迹,今天用 API 重建不出来。

<!-- TODO: 数据下载链接 -->
**下载:** _(待补)_

## 完整构建记录

<!-- TODO: blog 链接 -->
从零到停手的全过程 _(待补)_

## 交流

<!-- TODO: TG group 链接 -->
Telegram: _(待补)_

---

以上不构成任何投资建议。第三方行情数据以研究用途附带,其他用途请自行从源头重新获取。
