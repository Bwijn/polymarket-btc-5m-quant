# Polybot Trading 术语表

**用途**: 按需查阅。不是系统教材,只为看懂 polybot code 服务。每个术语尽量给 CS 类比 + 在 code 里哪个位置出现。

按依赖顺序分层,看的时候**从 Layer 0 开始往下**,上层概念都建立在下层之上。不要跳层。

---

## Layer 0: 市场的物理结构

这一层是所有 exchange 的通用语言。不懂这层下面全白搭。

### Orderbook(订单簿)
交易所的**状态数据结构**,里面按价格排序记录所有"有人愿意买/卖但还没成交"的订单。
> **CS 类比**: 一个双优先队列 —— 买方队列按价格降序(最高价在最前),卖方队列按价格升序(最低价在最前)。match engine 的工作就是不断 pop 两个队列顶部做撮合。

### Bid(买单)
orderbook **买方侧**的挂单。声明: "我愿意用 X 价格买 N 股,谁愿意卖给我都行"。
> **Poly 里**: 别人挂在 Over/Under token 上的买价。我们的 bot 不产生 bid,只**吃别人的 ask**。

### Ask / Offer(卖单)
orderbook **卖方侧**的挂单。声明: "我愿意用 X 价格卖 N 股,谁愿意买都行"。
> **Poly 里**: 我们 BUY 时,就是去吃这些 ask。`real.py` 里的 FOK BUY 本质是从 ask 队列里 pop。

### Best Bid / Best Ask(最高买价 / 最低卖价)
两个队列各自的**队首元素**。
- best bid = 买方愿意出的最高价
- best ask = 卖方愿意接受的最低价

### Spread(价差)
`best_ask - best_bid`。反映市场**流动性的紧密度**。
> spread 越小 = 流动性越好 = 交易成本越低
> Polymarket 典型 spread 是 1-3 分(0.01-0.03),冷门 market 能到 5-10 分。

---

## Layer 1: 订单的行为类型

### Limit Order(限价单)
指定价格,**不满足就挂到 orderbook 里当 resting order 等**。
> **CS 类比**: `pq.push((price, size))`, 不立即 return。

### Market Order(市价单)
不指定精确价格,**立刻吃当前 orderbook 里最划算的对手单**。
> **CS 类比**: `while budget > 0: pop best counterparty`
> **Poly 里**: 链上其实没有"真"市价单,SDK 把它翻译成"带 slippage cap 的 limit order + FOK"。

### Resting Order(挂着的订单)
已经进入 orderbook,正在等人来吃的订单。
> 我们 bot **不产生 resting order**,所有订单都是 FOK,要么当场吃、要么立即消失。

### FOK / IOC / GTC(订单生命周期)
签完订单提交到 CLOB 时必须指定一种:

| 类型 | 全称 | 行为 |
|---|---|---|
| **FOK** | Fill-or-Kill | 能一次吃满全部 → 成交;不能 → **整单 reject**,一股都不买 |
| **IOC** | Immediate-or-Cancel | 能吃多少吃多少,剩下的立即取消,不挂单 |
| **GTC** | Good-Till-Cancelled | 吃不到就挂到 book 里等,手动撤单前永远存在 |

> **Poly 里**: `real.py` 用 `OrderType.FOK`。copy trading 要就全成要就别成,不要 partial fill(partial 意味着仓位规模偏离我们的 $1 预期,风险失控)。

---

## Layer 2: 交易的角色

### Maker(挂单方)
把订单**放进 orderbook** 的人。**提供流动性**。
> 在 Poly 交易所的激励里,maker 通常享受 rebate(返佣) 或至少免 fee。

### Taker(吃单方)
**主动消耗 orderbook 里的 resting order** 的人。**消耗流动性**。
> 我们 bot **100% 是 taker**。taker 通常付 fee。
> Polymarket 当前 taker fee 规则见官方 doc,历史上有变动。

### Crossing the Spread(跨越价差)
taker 的动作描述。为了立即成交,taker 必须出一个价让自己的订单**跨越** spread —— BUY 侧出到 ≥ best ask,SELL 侧出到 ≤ best bid。

---

## Layer 3: 成交的数学

### Fill(成交) / Fill Price(成交价)
订单**实际**消耗的对手单所达成的每股价格。可能不同于下单时我们指定的任何价格。

### Weighted Average Fill(加权平均成交价)
当一笔订单**跨多个价档**成交时,fill price 是按消耗量加权的平均值。

> 示例: $1 BUY,cap=0.53
> 吃到: 0.5 shares @ 0.49 ($0.245) + 1.51 shares @ 0.50 ($0.755)
> 总 USDC = $1, 总 shares = 2.01
> **fill_price = 1.00 / 2.01 = 0.4975**(加权均价)
>
> **这就是为什么 `fill_price = makingAmount / takingAmount` 这个公式天然是加权的** —— making 和 taking 都是全局 total。

### Slippage(滑点)
**期望价 vs 实际价的偏差**,是所有"预测价格不准"引入的成本总和。

> 在 `real.py` 的 log 里:
> ```python
> slip = (fill_price - cur_price) / cur_price
> ```
> 这其实不是传统意义的 slippage,是 fill vs 下单前查到的 last trade 的偏差(一个粗糙的 slippage proxy)。

### Slippage Cap / Ceiling(滑点上限)
我们**能接受的最差成交价**。超过就宁可不成交。
> **Poly 里**: `MarketOrderArgs(price=cap)` 里的 `price` 字段就是这个 cap。对 BUY 是上限,对 SELL 是下限。

### Tick Size(最小价格单位)
orderbook 上两个相邻价档之间的**最小间隔**。
> Polymarket 大部分 market 的 tick 是 **0.01**(1 分),有些 neg-risk market 是 **0.001**。

---

## Layer 4: Polymarket CLOB 的特殊机制

### CLOB(Central Limit Order Book,中心化限价订单簿)
把所有订单汇总到一个共享的 orderbook 里,**按价格-时间优先级撮合**的交易所范式。
> 对立面是 AMM(自动做市商),用数学曲线定价(Uniswap 那种)。Polymarket 是 CLOB 派。

### Off-chain Matching(链下撮合)
**撮合引擎不是智能合约,是 Polymarket 自己的服务器**。
> 这样做的好处: match 快、不用 gas 费、可以做复杂规则(price-time priority 等)。坏处: 中心化信任,需要信任撮合服务器不作弊。

### On-chain Settlement(链上结算)
撮合完成后,Polymarket 把 match 结果**提交到 Polygon 上的合约**去执行资产交换。
> 所以**撮合免 gas,但成交有 gas**。Polymarket 给 maker 补贴 gas,taker 自己承担(或走 funder 地址)。

### Signed Order(签名订单)
一个**用私钥签过名的订单对象**,等同于"离线授权票据"。持有它的任何人都可以把它 broadcast 到合约去执行,且执行效果只影响签名者的地址。
> **CS 类比**: JWT token —— 离线生成,带签名,谁拿到都可以用,但作用范围只限于签名人授权的范围。
> **Poly 里**: `client.create_market_order(args)` 的返回值就是 SignedOrder 对象。

### EIP-712
**以太坊的结构化数据签名标准**。定义了"怎么把一个 dataclass 转成确定的 hash 再签名",保证签名的不可篡改性。
> 对我们: 是 SDK 内部细节,我们只要调用 `create_market_order` 就自动走 EIP-712,不用关心实现。

### Neg-risk Market
Polymarket 的一类特殊市场结构,一个 event 下多个互斥的 outcome 共享底层 collateral。比如"谁赢得 2028 大选"这种多人选项的 market 就是 neg-risk。
> 对我们代码: `create_market_order` 内部需要知道是否 neg-risk 来决定用哪个合约地址。SDK 有时会自动查询,有时要我们传 `options.neg_risk`。

---

## Layer 5: Polybot code 里的术语

这一层是我们自己命名的,不是通用术语,但理解 code 时会频繁遇到。

### Signal Price(信号价)
**whale 的历史成交价**,来自 Data API 返回的 trade 数据。
> 在 code 里是 `trade["price"]`,从 `ingestion/watcher.py` 产出,传给 `execution/real.py`。

### Current Price(当前价)
**我们查到的 last trade price**,来自 `get_last_trade_price()`。
> 这是"数据可知的最新成交价",但**不保证是此刻的 best ask** —— best ask 可能比 last trade 高几个 tick。

### Cap Price(限价上限)
**我们传给 SDK 的 `price` 参数**,作用是 slippage cap。
> 在 `real.py` 里是 `round(cur_price, 2)`。
> **todo.md item 3 指出的 bug**: `round()` 的 banker's rounding 可能把 cap 压到 cur 之下,导致 FOK 静默 reject。

### Drift(漂移)
`abs(cur_price - signal_price) / signal_price`,**相对偏差**。用作 drift guard 判据。
> `real.py` 里: `if drift > 0.10: skip`。
> 保护语义: "如果 whale 下单那一刻 vs 我们能跟上的此刻,价格已经偏离超过 10%,说明市场已经吸收掉 whale 的 alpha,我们跟进没意义"。

### Fill Price Sources(同一概念两个地方出现)
- **SDK 签名阶段**: `maker_amount` / `taker_amount`,是 SDK 内部变量,**我们 code 看不到**。代表"预期的"付出和收到。
- **API 响应阶段**: `makingAmount` / `takingAmount`,是 post_order 返回值的 JSON key,**我们 code 读**。代表"实际的"付出和收到。
> 两者拼写不同是因为 Python snake_case vs JSON camelCase。**两者数值可能不同** —— actual taking 可能多于预期 taker(因为 fill 比 cap 便宜,同样的 $1 买到了更多 shares)。

---

## Layer 6: 更抽象的金融概念(按需查阅)

### Alpha(超额收益)
**超过"基准"的那部分收益**。"基准"通常指大盘指数或无风险利率。
> 在我们场景里: whale 的 alpha = whale 战胜 market efficient pricing 的能力。我们 copy trade 的本质是**寄生在 whale 的 alpha 上**。
> 如果 whale 没 alpha(cur_price 和 signal_price drift > 10%, 说明市场已经跟进定价),我们的 drift guard 就会 skip。

### Liquidity(流动性)
**一个市场能以多小的成本完成多大的交易**。
> 流动性好的 market: spread 窄、orderbook 深度厚,$100 的交易不影响价格。
> 流动性差的 market: spread 宽、深度薄,$10 的交易就能推动 fill_price 跨好几个 tick。
> 我们 $1 仓位设计的初衷就是**绕开流动性约束**,任何 market 都能吃。

### Quote(报价)
一个"**声明我愿意以某价做某事**"的动作。
> 当你"post a quote" 时,你其实在 orderbook 里留下一个 maker order。
> 我们 bot **不 quote**,我们只 take(吃别人的 quote)。

### Rebate / Taker Fee(返佣 / 吃单费)
交易所激励结构的两头:
- **Rebate** = 给 maker 的**奖励**(返佣),鼓励提供流动性
- **Taker fee** = 向 taker **收费**,让 taker 为即时成交付费

> 当前 Polymarket 的 fee 结构见 官方 doc,历史上有调整,不要从记忆里引用。
> 我们是 100% taker,所以 fee 结构对我们是纯成本。

---

## 补充: 重要的"不要混淆"陷阱

### 1. `maker_amount` vs `makingAmount`
| | `maker_amount` | `makingAmount` |
|---|---|---|
| 出现位置 | SDK 本地变量(签名前) | post_order 响应的 JSON key |
| 数值来源 | SDK 从 MarketOrderArgs 算出来的 | CLOB 撮合引擎返回的实际成交值 |
| 我们 code 是否读 | 否(SDK 内部) | 是(fill_price 计算) |

### 2. Market Order ≠ Maker Order
- **Market Order** = 市价单,指"以市价立即成交"
- **Maker Order** = 挂单,指"在 book 里当流动性提供者"
- 这两个是**正交的维度**,market order 是 taker,maker order 是 maker。
- **命名恶心程度**: `create_market_order` 创建的是**市价单**(taker),不是"maker 订单"。

### 3. Signal / Current / Cap / Fill 是 4 个不同的价
| 名字 | 含义 | 来源 |
|---|---|---|
| signal_price | whale 的历史成交价 | Data API |
| cur_price | 查到时的 last trade | CLOB `/last-trade-price` |
| cap_price | 我们挂的 slippage 上限 | `round(cur_price, 2)` |
| fill_price | 实际成交的每股价 | `makingAmount / takingAmount` |

它们通常接近但**都不相等**,整条链上的 drift 叠加决定了最终 copy trading 的成本结构。

---

**维护约定**: 这个 doc 只增不随意删。用户(INTJ,CS/RE 背景)按需查阅,我不主动扩充新 Layer,遇到具体代码问题需要新术语时再回来补。
