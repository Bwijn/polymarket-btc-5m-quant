## Global Protocols
- **交互语言**：工具与模型交互强制使用 **English**；用户输出强制使用 **中文**。 (因为English是最精准的, 没有歧义的)
- **Jargon 翻译 (术语翻译, high-frequency repetition 高频重复)**：任何英文术语/缩写**每次出现**都要带中文翻译，格式 `term (中文)`，例：`sunk cost (沉没成本)` / `ex-post rescue (事后救援)` / `OOS = out-of-sample (样本外)` / `FWE = family-wise error (族系误差率)`。**不做首次/后续区分**，哪怕同一回复内出现 5 次也要重复翻译 5 次。目的：让用户通过高频重复刺激形成英文肌肉记忆 (muscle memory)，不是省字数。只有用户在当前回复中主动使用过的术语可省略翻译。
- **风格定义**：整体代码风格**始终定位**为，精简高效、毫无冗余。该要求同样适用于注释与文档，且对于这两者，严格遵循**非必要不形成**的核心原则。
- **Alignment pause protocol** 用户说 "align"/"暂停"/"on the same page" 时进入对齐模式：只回答问题，不执行任何操作(probe/scan/decompile)。等用户明确说 "go"/"继续"/"resume" 才恢复执行。原因：用户需要重建 mental model，LLM 执行太快会导致用户跟丢推理链。
- **ADHD-friendly SSOT dashboard** 用户 ADHD, mental model 重建成本高. 任何"当前状态"必须可通过 db tables / views **1 行 SQL 查询**获得, 不要求用户翻找 / 拼凑 / 跨多个 dir 整合. 3 个落地原则:
  1. **3 流分置**: data → db tables, code/rules → `polybot/`, prose docs → `docs/`. 不混. 
     SPEC 这种混合体拆成 db 行 (quant metric) + code function (decision rule) + markdown (rationale prose).
  2. **todo.md 只留 todo**: 完成的 item 不堆 audit 区 (用 git log 做 audit). 减视觉噪声.
  3. **DB-as-Bloomberg-terminal**: 表 schema 命名自文档化 (e.g., `factor_decisions`, `mining_runs_v13_legacy`, `_legacy_copytrade_paper` 前缀), 用 db viewer (DBeaver) 看板替代 web dashboard. 加 derived view (e.g., `factor_current_state`) 给"当前快照"用.
- **No blackbox** 永远不要给用户黑箱操作。每一步操作都要解释**为什么**这么做，底层原理是什么。用户要靠这套系统谋生，必须能逐行理解每一行在干什么。不解释的执行 = 不合格。
- **No third-party wrapper SDK** 涉及真金白银的依赖只用官方SDK（V2 era：`py-clob-client-v2`，V1 cutover 4/28/2026 后 V1 SDK `py-clob-client` 已不能签名 V2 orders）。禁止安装第三方 wrapper / "便利包"（如 polymarket-apis, polybot-toolkit, awesome-pm 等高 star 但非官方的封装），X/Reddit 已多人中招报告 backdoor / 私钥窃取 / silently 改 order params。官方 SDK 与直接调用 PM endpoints (httpx) 都允许：CLOB 写操作（下单 / cancel / withdraw）必须用官方 SDK；公开 read endpoint（Gamma metadata, CLOB prices-history 等）httpx 手写也可，不强制 SDK。
- **Deploy safety** 本地（/home/polymarket_work/polybot/）为开发环境，VPS（/opt/polybot/）为生产环境。部署时只推代码文件，**严禁覆盖VPS上的polybot.db**，数据库是不可替代的数据资产。部署统一使用 `bash /home/polymarket_work/deploy.sh`（rsync + systemctl restart），禁止手动 scp 或重复编写部署脚本。
- **DB 职责隔离 (dev vs prod, 两 db 各司其职, 永不混)**:
  | DB 路径 | 角色 | 内容 | 谁写 |
  |---|---|---|---|
  | `/home/polymarket_work/scratch/data/pm_btc5m.db` | **dev / research workspace** | raw ingest (events, trades, binance_*), mining (factors, mining_runs), analysis (paper_candidates), decision audit (factor_decisions) | mining 脚本 + 分析脚本, 本地 only |
  | `/opt/polybot/polybot.db` (VPS) / `/home/polymarket_work/polybot/polybot.db` (本地空 schema) | **prod / paper-trade runtime** | **只**记录 paper trade 实战数据 (paper_trade_5m_binary 等) | scanner.py 运行时写 |
  
  ❌ 禁止: 把 mining/analysis 表加到 polybot.db, 或把 paper trade 表加到 pm_btc5m.db.
  ❌ 禁止: 跨 db 互查 (e.g. polybot.db join 到 factor_decisions). 决策表查 pm_btc5m.db, paper 表查 polybot.db, 不互通.
  ✓ 推荐: 同步 VPS polybot.db 到本地用 `bash sync_paper_db.sh` (rsync 拉到 backups/, 不覆盖本地 schema 文件).
  ✓ 推荐: dev → prod 决策传递走**代码** (strategies.py 改 ACTIVE) + deploy.sh, **不**走 db 同步.
- **API docs workflow (强制顺序, 不可跳步)**:
  1. **llms.txt 是 master index (主目录)**: https://docs.polymarket.com/llms.txt 列出**所有** endpoint docs + operational docs (rate-limits, changelog, errors, schemas, host 列表). 任何 API 决策**第一步 fetch llms.txt**, 在里面 grep 关键词 (rate / batch / fidelity / trades / book ...), 才 follow 具体页 link. **不要凭印象猜 URL 路径**, 也不要把 llms.txt 当普通页平等对待 — 它是入口, 其它都是从它索引出去的子页.
  1a. **Docs fetch 用 `curl` + `Read`, 不用 `WebFetch`**: WebFetch 不是直接给我 raw 文本, 它把 raw 喂给一个**小模型 (sub-model) 按我的 prompt 总结**, 我看到的是二手转述, 信息**有损 (lossy)** 且可能 hallucinate (幻觉). docs 是 SSOT (single source of truth), 必须 raw text. 用 `curl -s URL > /tmp/docs.md && cat /tmp/docs.md` 或 Bash → 自己 grep / Read. WebFetch 适合大型网页快速摘要, 不适合 docs / schema / config 这种**字面精确度 (literal precision) 重要**的内容.
  2. **Operational docs 同样必查, 不是次要**: rate-limits.md / changelog.md / 错误码 / host 列表 都在 llms.txt 里. 写 ingest / batch / concurrency / 高频 query 之前**必须**查 rate-limits.md, 凭印象限速 = 浪费容量 (实测 PM /prices-history 1000 req/10s 上限, 我们曾误用 1.3 req/s = 0.13% 利用率). 用 SDK 也不能跳过 — endpoint 限速跟 SDK 无关.
  3. **PM 多 host 别混**: gamma-api (元数据 metadata) / clob (订单簿 + prices-history) / data-api (trades + analytics). 路径搞错 = 401 / 404. host 列表在 llms.txt.
  4. **真实 response 是 SSOT (single source of truth)**: docs 可能过时 (如 `ORDER_STATUS_MATCHED` 实际 `MATCHED`). 涉及资金的 enum/field, 以实际录制 response 为准, 不以 docs 为准. probe 不能跳过 docs 但 docs 不能跳过 probe — **两步都要**.
  5. **Major version 后强制 re-fetch llms.txt**: V1→V2 cutover 时整个 index 重排, 旧链接死, informal filter (如 `series_slug`) 砍掉. re-fetch 是 mandatory 不是 optional.
  6. **SDK 是 wrapper 不是 docs 替代**: SDK 语义 = endpoint 语义, enum / edge case / 限速 / lag (滞后) 都仍需查 docs. SDK 没 wrap 的新 endpoint, httpx 直调公开 read endpoint 合规.
- **Python 运行器统一用 uv，依赖进 pyproject、复用持久 venv** 脚本一律 `uv run python <script>`。禁止：`python3 script.py`（system python 无依赖）、手动激活 venv、`uv run --with`（每次重新 resolve 的临时环境、非持久、等同 npx —— 别被"省事"误导）。依赖一律 `uv add` 进项目 `pyproject.toml`，装入持久 venv 复用。redeem / bot 相关 scratch 脚本借 polybot 环境跑：`uv run --project polybot python scratch/xxx.py`；mining / 研究脚本用 root `.venv`。
- **WSL storage 三层观察, 不准只看 `df -h /mnt/c`**: 跑在 WSL2 里, 存储是三层嵌套, 不是一个数字能描述:
  1. **WSL 内部 ext4 used** (`df -h /` 的 used): 真实文件占用, e.g. 77G
  2. **vhdx 物理文件大小** (Windows 端看 `ext4.vhdx`): 历史最大水位, 只增不减, e.g. 139G. 用 PowerShell `Optimize-VHD` 可手动回收已删除空间
  3. **vhdx max grow cap** (`df -h /` 的 Size): WSL2 配置软上限, default 1007G, 可改 `.wslconfig`
  
  **真实 ingest budget = min(cap 1007G - vhdx 当前 139G, C: free 66G)** — C: 才是硬瓶颈, 因为 vhdx 在 C: 上 grow.
  
  ❌ 错: 只看 `df -h /mnt/c` 显示 "C: 87% 满, 66G free" 就判 "存储紧"
  ❌ 错: 只看 `df -h /` 显示 "880G 可用" 就判 "存储宽裕" (那是软 cap, 不是物理 free)
  ✓ 对: 三层一起看. ingest 前明确 budget 是 vhdx 还能 grow 多少 (受 C: free 限制).
  
  教训: 因这个误读, 给 V1 trades 选了 slim schema (5 字段) 而不是 fat (8 字段), 后来推翻重做, 浪费 90min + 5GB 已 ingest 数据.

## Trading Constitution
- **Data > narrative** 加/减 wallet 必先查 paper EV。任何"他是 X 流派/head/tail"的观察都是 hypothesis，paper EV 是 test。禁用 priors-override-data。看到 behavior pattern 想做决策 → STOP → 查 EV → 再决定。
- **Priority order** EV > MDD > Kelly > Sharpe。EV 正才玩，负即走。其他都是二阶精修。
- **bt carry-forward 失真 (TEMP — infra 修复后删此条)** bt (backtest, 回测) dataset 的 entry 价取自 PM prices-history `fidelity=1` (1 分钟分辨率, API 物理下限), 历史 per-event book (订单簿) PM 不提供、找不到 → 只能 carry-forward (前值填充), entry 价最多 ~60s 陈旧. 后果: **bt EV (expected value, 期望值) 数值不可信**, 实测 bt→paper gap (差距) ~−0.11 (2026-05). 两层机制: ① et=0s (entry 在 K 线开盘) factor 直接吃 ~−7% 执行惩罚 (开盘瞬间价最陈旧); ② et>0s factor 主要死于 overfit (过拟合) — noisy (噪声大) 的 bt 指标让多轮选择虚高. 规则: (a) bt 只作 coarse pre-filter (粗筛), 永不信其 EV 数值; (b) paper (模拟盘) 才是真 OOS (out-of-sample, 样本外); (c) et=0s 候选额外扣 ~−7% haircut (折价), 优先 et>0s; (d) 拓宽 paper funnel (漏斗), 别信 bt top-N. 修法: 历史 book 无解. forward (向前) 可修但需**新建 infra** — 现 WS (WebSocket) 只在 trigger (触发) 时取一次瞬时 best ask 塞进 paper 行, 不持久化 book 时序; 要修需建 book recorder (订单簿录制器): 对**每根 candle (不只触发的)** 在各 entry offset 持久化真实 ask, 攒成真实 book 数据集供未来 mining (挖掘); 该数据集成熟后删此条.