## Global Protocols
- **交互语言**：工具与模型交互强制使用 **English**；用户输出强制使用 **中文**。 (因为English是最精准的, 没有歧义的)
- **Jargon 翻译 (术语翻译, high-frequency repetition 高频重复)**：任何英文术语/缩写**每次出现**都要带中文翻译，格式 `term (中文)`，例：`sunk cost (沉没成本)` / `ex-post rescue (事后救援)` / `OOS = out-of-sample (样本外)` / `FWE = family-wise error (族系误差率)`。**不做首次/后续区分**，哪怕同一回复内出现 5 次也要重复翻译 5 次。目的：让用户通过高频重复刺激形成英文肌肉记忆 (muscle memory)，不是省字数。只有用户在当前回复中主动使用过的术语可省略翻译。
- **风格定义**：整体代码风格**始终定位**为，精简高效、毫无冗余。该要求同样适用于注释与文档，且对于这两者，严格遵循**非必要不形成**的核心原则。
- **Import 风格强制 direct submodule import (直接子模块导入), 禁 re-export barrel (再导出桶文件 / `__init__.py` facade 门面)**: 一律 `from pkg.sub import name`——从符号**定义所在模块**直接导入, provenance (来源) 显式 + import-order (导入顺序) 无耦合。**禁**在 `__init__.py` 里 `from .sub import name` 做 re-export (再导出) 让 `from pkg import name` 生效 (= barrel/facade 反模式: 藏符号真实位置 + 埋导入顺序坑 + 同包两种进法不统一)。`__init__.py` 只放 package docstring, 零 re-export / 零逻辑。先例: `polybot/lib/compute/__init__.py` 从 barrel 清成裸 docstring, 全库统一 (2026-07)。
- **Alignment pause protocol** 用户说 "align"/"暂停"/"on the same page" 时进入对齐模式：只回答问题，不执行任何操作(probe/scan/decompile)。等用户明确说 "go"/"继续"/"resume" 才恢复执行。原因：用户需要重建 mental model，LLM 执行太快会导致用户跟丢推理链。
- **ADHD-friendly SSOT dashboard** 用户 ADHD, mental model 重建成本高. 任何"当前状态"必须可通过 db tables / views **1 行 SQL 查询**获得, 不要求用户翻找 / 拼凑 / 跨多个 dir 整合. 3 个落地原则:
  1. **3 流分置**: data → db tables, code/rules → `polybot/`, prose docs → `docs/`. 不混. 
     SPEC 这种混合体拆成 db 行 (quant metric) + code function (decision rule) + markdown (rationale prose).
  2. **todo.md 只留 todo**: 完成的 item 不堆 audit 区 (用 git log 做 audit). 减视觉噪声.
  3. **DB-as-Bloomberg-terminal**: 表 schema 命名自文档化  用 sqlite 看板替代 web dashboard. 
- **No blackbox** 永远不要给用户黑箱操作。每一步操作都要解释**为什么**这么做，底层原理是什么。用户要靠这套系统谋生，必须能逐行理解每一行在干什么。不解释的执行 = 不合格。
- **No third-party wrapper SDK** 涉及真金白银的依赖只用官方SDK（V2 era：`py-clob-client-v2`，V1 cutover 4/28/2026 后 V1 SDK `py-clob-client` 已不能签名 V2 orders）。禁止安装第三方 wrapper / "便利包"（如 polymarket-apis, polybot-toolkit, awesome-pm 等高 star 但非官方的封装），X/Reddit 已多人中招报告 backdoor / 私钥窃取 / silently 改 order params。官方 SDK 与直接调用 PM endpoints (httpx) 都允许：CLOB 写操作（下单 / cancel / withdraw）必须用官方 SDK；公开 read endpoint（Gamma metadata, CLOB prices-history 等）httpx 手写也可，不强制 SDK。
- **Deploy safety** 本地（/home/polymarket_work/polybot/）为开发环境，VPS（/opt/polybot/）为生产环境。部署时只推代码文件，**严禁覆盖VPS上的polybot.db**，数据库是不可替代的数据资产。部署统一使用 `bash /home/polymarket_work/deploy.sh`（rsync + systemctl restart），禁止手动 scp 或重复编写部署脚本。
- **DB 职责隔离 (本地全 `/home/polymarket_work/db/`, 各司其职, 永不混)**:
  | DB 路径 | 角色 | 内容 | 谁写 |
  |---|---|---|---|
  | `/home/polymarket_work/db/pm_btc5m.db` | **dev / research workspace** | events + binance_* (raw ingest), factors (状态注册表), **ep_panel** (EP source-of-record: cid PK + 40 p_intra 列 = dropped trades 表的 slim 继任者; ETL = /trades→compute in-flight→ep_panel→投影进 features.parquet, raw 不存), _legacy_*/_ingest_v3_*. (**trades 表 2026-06-17 dropped**, db 79G→286MB) | mining 脚本 + 分析脚本, 本地 only |
  | `/home/polymarket_work/db/polybot_live.db` | **VPS prod sync 本地副本 (read-only)** | factor_roster, factor_log, paper_trade_5m_binary, feature_history (镜像) | `bash sync_paper_db.sh` rsync 自 VPS |
  | `/opt/polybot/polybot.db` (VPS) | **prod runtime SSOT — 含 roster** | **factor_roster (arming 权威) + factor_log (rationale)**, scanner.py 实时写入 paper / live trade | scanner.py on VPS + 人手改 roster |
  
  ❌ 禁止: 把 mining/analysis 表加到 polybot 实战 db, 或把 paper trade 表加到 pm_btc5m.db.
  ❌ 禁止: 全库 copy / backup pm_btc5m.db. 改某张表的 schema / 数据, **只 dump 目标表**: `sqlite3 db .dump <table> > t.sql` (小表瞬间, = 精确 rollback artifact 回滚物). 整库 copy 仅在迁移**整个** db 时才允许. 
  ✓ 推荐: `bash sync_paper_db.sh` 同步 VPS polybot.db → 本地 `db/polybot_live.db`. 不覆盖 pm_btc5m.db.
- **Roster = data** — arming 权威 = VPS `/opt/polybot/polybot.db` 的 `factor_roster`. 改 roster **直接改 prod db**, 不走 deploy / 不走 dump / 无 git 历史 (明知选的):
  ```
  ssh vps "sqlite3 /opt/polybot/polybot.db \"UPDATE factor_roster SET status='live' WHERE label='R4'\""
  ssh vps "systemctl restart polybot"
  ```
  research 侧读 roster 走 `db/polybot_live.db`, 先 `bash sync_paper_db.sh` 免得对着过期 roster 跑 dedup.
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
- **Gates SSOT** — 所有 bt / paper / live gate 数字定义在 `polybot/lib/gates.py` (含 rationale + 实测来源 + chain self-test). 文中提到的具体数字 (5%, 7%, t>1.65, n=800 ...) 都是当时 git 版本; 修改通过 git commit + audit message 跟踪。**不要在其它地方 hardcode 重复**, 全部 `from polybot.lib.gates import ...`.
- **Factor admission — 只做「高 edge · 可验证 · 值得做」的 factor** capital 小 + PM crypto 7% taker fee (吃单费, 全平台最高档) → **无法复刻 Simons 式小 edge 复利**: 那需 edge 被证到铁律级 + 单笔成本≈0 + 千万级交易量, 三者皆无 (Simons 的 50.75% 能赚是因它被证到 100% 确定且成本≈0, 不是"edge 小也行"). 一个 factor 允许进 live 当且仅当三条全满足: ① **可验证** — 能在可行 n 内统计证明 (见下条 gate); ② **够大** — net EV (扣 fee + drift) 点估计 ≥ `PAPER_TO_LIVE_NET_EV` (gates.py); ③ **实测** — 证据来自 paper (真 OOS), 不是 bt. 小 edge 对我们结构性不可行 (fee 吃光 + 可行 n 内证不出).
- **paper→live gate (kill-unless-proven, 举证责任在 factor)** 默认 = 不给 live; factor 必须主动证明 edge 才能上. "bt 好看 / paper 战绩好看 / 凭感觉" ≠ proven. 判据是**一条固定规则, 不是固定数字** — 2 个 dial 全 factor 统一 (不逐个调): 信心档 net EV 的 t (统计量) > `PAPER_TO_LIVE_T_STAT`; magnitude hurdle 点估计 ≥ `PAPER_TO_LIVE_NET_EV`. 每 ~50 单 checkpoint: GRADUATE = t 过 ∧ EV 过 → live; KILL = n 到 `PAPER_TO_LIVE_CAP_N` 仍未 graduate (edge 即便真也太小/太慢, 不值), 或挂钟过 `PAPER_TO_LIVE_CAP_WEEKS`, 或 CI 已明确转负. live 中 factor 持续受同一 gate, decay 跌破即降回 paper. H5 是反面教材: legacy factor, paper +3% net / t=0.44 (远不达 gate), 当初靠"paper 好看" 凭感觉直接 live, 违反此条 → killed (见 factors 表 status='killed' + note/diag).