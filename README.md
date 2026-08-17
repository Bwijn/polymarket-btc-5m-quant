# polymarket-btc-5m-quant

An end-to-end quant stack for Polymarket's BTC 5-minute binary markets — factor
mining, feature engineering, paper harness, live execution. Archived: the project
is discontinued, everything is published including the data and the failures.

Polymarket BTC 5 分钟二元市场的完整量化系统。**项目已停,全部公开** —— 代码、数据、
以及所有被证伪的因子。

写这份 README 的目的不是展示成果,是把踩过的坑留下来,让后来的人少花时间。

---

## 结论先行

3.5 个月样本外 paper 交易,3256 笔已结算:

| | |
|---|---|
| 进 roster 的因子 | 43 |
| 被 gate 杀掉 | **27** |
| 升级到实盘 | **0** |
| 毛 EV | −1.96% |
| **净 EV(扣费后)** | **−4.95%** |

**7% 的 taker fee (吃单费) 就是全部答案。** 毛 EV 本来就微负,费用把它推到每笔 −5%。

这不是"再调调参数就能转正"的问题。在 7% 手续费的场子里,**小 edge (优势) 对小资金是
结构性不可行的** —— 不是"难",是在任何可达到的样本量内都无法证明它大到能覆盖成本。
这条如果你在做同类项目,值得先算清楚再动手。

---

## 踩过的坑

### 1. PM `/trades` 有 4000 条硬上限,而且**静默截断**

最贵的一个坑。早期 ingest 用 `limit=500` × 3 个 offset = 1500 条上限,以为够了。

2026-05-23 实测探到真实边界:`limit≤1000` 是**静默生效**的(传 5000 不报错,只给你 1000),
`offset≤3000` 是强制的 —— 所以单个 cid 最多只能取到 4000 条。

后果:**98.6% 的市场早盘成交没被抓到**,导致回测的 entry price (入场价) 估计整体有偏。
修复脚本在 [`research/ingestion/ingest_pm_trades_v3_4kcap_backfill_20260523.py`](research/ingestion/ingest_pm_trades_v3_4kcap_backfill_20260523.py)。

**教训:分页参数必须 probe (探测) 真实边界,不能信文档也不能信"没报错"。静默截断不会告诉你。**

### 2. 限速凭印象猜 = 白白浪费容量

写 ingest 之前没查 rate-limits 文档,凭感觉限到 1.3 req/s。实测 data-api 是 **200 req/10s**,
CLOB 的 `/prices-history` 是 **1000 req/10s** —— 相当于只用了 0.13% 的配额。

**教训:官方文档有 `llms.txt` 索引,rate-limits 是独立一页。写任何批量任务前先查,别猜。
用了 SDK 也一样,限速是 endpoint 的属性,跟你用什么客户端无关。**

### 3. 文档写的 enum 和真实返回不一样

文档说 `GET /data/order/{id}` 返回 `status: "ORDER_STATUS_MATCHED"`,实际返回的是
`"MATCHED"`,没有前缀。

后果:11 笔已成交的单子永远卡在 pending 状态,$11 锁在托管里出不来,钱包被饿死。

**教训:涉及资金的 enum,以实际录制的 response 为准,不以文档为准。文档是起点,不是终点 ——
docs 不能跳过 probe,probe 也不能跳过 docs,两步都要做。**

### 4. `success: true` 不等于成交

FOK (Fill-Or-Kill) 订单可能返回 `success: true` + `status: "delayed"`。"delayed" 的意思是
**钱已经出去了**,只是异步撮合。

如果你只在 `status == "matched"` 时记账,这笔钱就消失在账本外了。

**教训:任何 `success: true` 都意味着钱可能已经花掉,必须落库。不要假设某个特定状态字符串
是唯一的成功态。**

### 5. 先记账,再执行(reserve-before-execute)

上面几个 bug 的共同根因是同一个:**假设外部 API 的行为,而没有验证过**。

架构上的解法是把顺序固定死:

1. 先在 DB 写入意图(`status='pending'`)
2. 再调外部 API
3. 按结果更新 DB
4. 异常时清理这条预留

这样即使进程在中途崩溃,去重和状态追踪也不会有盲区。**真金白银的代码要对「未知状态」安全,
不只是对「已知状态」安全 —— mock 测试只能验证你已经知道的逻辑。**

### 6. 不装第三方 wrapper SDK

只用官方 SDK,公开的只读 endpoint 用 `httpx` 直接调。**不装任何"便利封装"**,哪怕 star 很多。

X 和 Reddit 上已经有多起报告:第三方 Polymarket 封装包里藏后门、偷私钥、悄悄改订单参数。
下单 / 撤单 / 提现这类写操作,只走官方 SDK。

### 7. WSL 的存储要看三层,只看一个数字必错

WSL2 里存储是三层嵌套:① ext4 内部实际占用 ② vhdx 物理文件大小(**只增不减**,是历史最高
水位) ③ vhdx 的增长上限(默认 1007G,软限制)。

真实预算 = min(增长上限 − vhdx 当前大小, Windows C 盘剩余空间)。**C 盘才是硬瓶颈**,因为
vhdx 长在上面。

当时只看了一个数字就判断"存储紧张",给 trades 表选了精简 schema 而不是完整的,后来推翻重做,
浪费 90 分钟 + 已经 ingest 的 5GB。

### 8. 因子准入:默认拒绝,举证责任在因子

不是"看起来不错就上"。所有阈值集中在 [`polybot/lib/gates.py`](polybot/lib/gates.py),
全局统一、不给单个因子调参:

| Gate | 值 | 含义 |
|---|---|---|
| `PAPER_TO_LIVE_NET_EV` | 0.05 | 净 EV 点估计要过 5% |
| `PAPER_TO_LIVE_T_STAT` | 1.65 | 净 EV 的 t 统计量 |
| `PAPER_TO_LIVE_CAP_N` | 800 | 到 800 单还没毕业就杀掉 |
| `PAPER_TO_LIVE_CAP_WEEKS` | 10 | 挂钟超时也杀 |
| `FACTOR_DEDUP_JACCARD_MAX` | 0.75 | 和已上线因子重叠过高则拒绝 |

**在可行样本量内证不出来的 edge,就算它是真的也要杀** —— 太慢的优势在 7% 手续费面前一文不值。

27/43 就是这么被杀掉的。这套 gate 唯一的产出是"什么都没让上线",而这恰恰说明它在工作。

---

## 这套系统解决了什么

- **一个真的跑在生产上的栈**:systemd 常驻、websocket 订阅、下单、结算、redeem (赎回),
  不是 notebook 里的回测玩具
- **因子表达式求值器** ([`lib/expr_eval_v1.py`](polybot/lib/expr_eval_v1.py)):因子定义是
  数据不是代码,加因子不用改程序
- **研究与实盘共用同一份计算逻辑** ([`lib/compute/`](polybot/lib/compute/)):避免回测和实盘
  算出不同的特征
- **一份拿不到第二次的数据**:`ep_panel` 是从 PM `/trades` 的回溯窗口里算出来的,那个窗口
  现在已经关了 —— 这 42,224 个市场的盘中价格轨迹,今天用 API 重建不出来

## 数据

体积原因不在仓库里,单独分发:

| 数据集 | 内容 |
|---|---|
| `pm_btc5m.db` | 42,224 个市场、`ep_panel`(40 列盘中价格)、215,388 根 Binance K 线、资金费率 / 持仓量 / 多空比 |
| `features.parquet` | 42,224 行 × 292 个特征 |
| `polybot_live.db` | `factor_roster`(43 个因子含 status)、`factor_log`(每个被杀因子的理由)、3,560 笔 paper 成交明细 |

<!-- TODO: 数据下载链接 -->
**下载:** _(待补)_

## 完整构建记录

<!-- TODO: blog 链接 -->
从零到停止的全过程 _(待补)_

## 联系

<!-- TODO: TG group 链接 -->
Telegram: _(待补)_

---

**免责声明:这套系统没有赚钱。** 这里没有任何投资建议,因子被记录下来正是因为它们被证伪了。
第三方行情数据以研究用途附带,其他用途请自行从源头重新获取。
