## Global Protocols
- **交互语言**：工具与模型交互强制使用 **English**；用户输出强制使用 **中文**。 (因为English是最精准的, 没有歧义的)
- **Jargon 翻译 (术语翻译, high-frequency repetition 高频重复)**：任何英文术语/缩写**每次出现**都要带中文翻译，格式 `term (中文)`，例：`sunk cost (沉没成本)` / `ex-post rescue (事后救援)` / `OOS = out-of-sample (样本外)` / `FWE = family-wise error (族系误差率)`。**不做首次/后续区分**，哪怕同一回复内出现 5 次也要重复翻译 5 次。目的：让用户通过高频重复刺激形成英文肌肉记忆 (muscle memory)，不是省字数。只有用户在当前回复中主动使用过的术语可省略翻译。
- **风格定义**：整体代码风格**始终定位**为，精简高效、毫无冗余。该要求同样适用于注释与文档，且对于这两者，严格遵循**非必要不形成**的核心原则。
- **Alignment pause protocol** 用户说 "align"/"暂停"/"on the same page" 时进入对齐模式：只回答问题，不执行任何操作(probe/scan/decompile)。等用户明确说 "go"/"继续"/"resume" 才恢复执行。原因：用户需要重建 mental model，LLM 执行太快会导致用户跟丢推理链。
- **No blackbox** 永远不要给用户黑箱操作。每一步操作都要解释**为什么**这么做，底层原理是什么。用户要靠这套系统谋生，必须能逐行理解每一行在干什么。不解释的执行 = 不合格。
- **No third-party wrapper SDK** 涉及真金白银的依赖只用官方SDK（V2 era：`py-clob-client-v2`，V1 cutover 4/28/2026 后 V1 SDK `py-clob-client` 已不能签名 V2 orders）。禁止安装第三方 wrapper / "便利包"（如 polymarket-apis, polybot-toolkit, awesome-pm 等高 star 但非官方的封装），X/Reddit 已多人中招报告 backdoor / 私钥窃取 / silently 改 order params。官方 SDK 与直接调用 PM endpoints (httpx) 都允许：CLOB 写操作（下单 / cancel / withdraw）必须用官方 SDK；公开 read endpoint（Gamma metadata, CLOB prices-history 等）httpx 手写也可，不强制 SDK。
- **Deploy safety** 本地（/home/polymarket_work/polybot/）为开发环境，VPS（/opt/polybot/）为生产环境。部署时只推代码文件，**严禁覆盖VPS上的polybot.db**，数据库是不可替代的数据资产。部署统一使用 `bash /home/polymarket_work/deploy.sh`（rsync + systemctl restart），禁止手动 scp 或重复编写部署脚本。
- **API docs workflow (强制顺序, 不可跳步)**:
  1. **llms.txt 是 master index (主目录)**: https://docs.polymarket.com/llms.txt 列出**所有** endpoint docs + operational docs (rate-limits, changelog, errors, schemas, host 列表). 任何 API 决策**第一步 fetch llms.txt**, 在里面 grep 关键词 (rate / batch / fidelity / trades / book ...), 才 follow 具体页 link. **不要凭印象猜 URL 路径**, 也不要把 llms.txt 当普通页平等对待 — 它是入口, 其它都是从它索引出去的子页.
  1a. **Docs fetch 用 `curl` + `Read`, 不用 `WebFetch`**: WebFetch 不是直接给我 raw 文本, 它把 raw 喂给一个**小模型 (sub-model) 按我的 prompt 总结**, 我看到的是二手转述, 信息**有损 (lossy)** 且可能 hallucinate (幻觉). docs 是 SSOT (single source of truth), 必须 raw text. 用 `curl -s URL > /tmp/docs.md && cat /tmp/docs.md` 或 Bash → 自己 grep / Read. WebFetch 适合大型网页快速摘要, 不适合 docs / schema / config 这种**字面精确度 (literal precision) 重要**的内容.
  2. **Operational docs 同样必查, 不是次要**: rate-limits.md / changelog.md / 错误码 / host 列表 都在 llms.txt 里. 写 ingest / batch / concurrency / 高频 query 之前**必须**查 rate-limits.md, 凭印象限速 = 浪费容量 (实测 PM /prices-history 1000 req/10s 上限, 我们曾误用 1.3 req/s = 0.13% 利用率). 用 SDK 也不能跳过 — endpoint 限速跟 SDK 无关.
  3. **PM 多 host 别混**: gamma-api (元数据 metadata) / clob (订单簿 + prices-history) / data-api (trades + analytics). 路径搞错 = 401 / 404. host 列表在 llms.txt.
  4. **真实 response 是 SSOT (single source of truth)**: docs 可能过时 (如 `ORDER_STATUS_MATCHED` 实际 `MATCHED`). 涉及资金的 enum/field, 以实际录制 response 为准, 不以 docs 为准. probe 不能跳过 docs 但 docs 不能跳过 probe — **两步都要**.
  5. **Major version 后强制 re-fetch llms.txt**: V1→V2 cutover 时整个 index 重排, 旧链接死, informal filter (如 `series_slug`) 砍掉. re-fetch 是 mandatory 不是 optional.
  6. **SDK 是 wrapper 不是 docs 替代**: SDK 语义 = endpoint 语义, enum / edge case / 限速 / lag (滞后) 都仍需查 docs. SDK 没 wrap 的新 endpoint, httpx 直调公开 read endpoint 合规.
- **Python 运行器统一用 uv** 所有 scratch/probe/cli 脚本一律 `uv run python <script>` 或 `uv run --with <pkg> python <script>`。禁止 `python3 script.py`（system python 没 httpx 等依赖）或手动激活 venv。`uv run` 会自动探测 pyproject.toml 的 venv；一次性依赖用 `--with`。

## Trading Constitution
- **Data > narrative** 加/减 wallet 必先查 paper EV。任何"他是 X 流派/head/tail"的观察都是 hypothesis，paper EV 是 test。禁用 priors-override-data。看到 behavior pattern 想做决策 → STOP → 查 EV → 再决定。
- **Priority order** EV > MDD > Kelly > Sharpe。EV 正才玩，负即走。其他都是二阶精修。
- **Hypothesis spec-first**挖到的factor 经过多层gate过滤后的survivors, 新 hypothesis 第一件事是按 `scratch/H_SPEC_TEMPLATE.md` 的 6-slot MVP 填写到 `scratch/H<N>_<topic>/SPEC.md`