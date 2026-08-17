[中文](README.md) · **English**

# polymarket-btc-5m-quant

An end-to-end quant stack for Polymarket's BTC 5-minute binary markets — factor mining,
feature engineering, paper-trading harness, live execution.
**Production-deployed, traded with real money, now fully open-sourced — code, data, and
factors included.**

---

## Battle-tested in production, mature, and ready to extend

Half a year of work. **Every line was reviewed by hand** — this is not something a model
generated and got shipped unread.

**It ran continuously in production for over four months** (2026-04-08 → 2026-08-16). Not a
demo that worked once for a screenshot:

- systemd daemon on a VPS, websocket order-book subscriptions, automated ordering,
  settlement and redemption — reconnect logic, idempotent dedup and crash recovery all
  hardened against real failures
- **615 real-money fills** (298 in the early copy-trade phase, net positive; 317 live on
  the 5m binary system)
- 3,560 out-of-sample paper trades across 3.5 uninterrupted months
- Full intra-candle price paths for 42,224 markets; 15 factor families, 292 engineered
  features

Every number here is one SQL query away in the published database.

### Why it is easy to pick up and extend

**4,974 lines of Python across 48 files** — small enough for one person to read end-to-end
in a few days, not a monolith nobody dares touch. A few design decisions mean extending it
does not require a rewrite:

| Design | What it buys you |
|---|---|
| **Factors are data, not code** ([`expr_eval_v1.py`](polybot/lib/expr_eval_v1.py) + the `factor_roster` table) | Adding a factor is one SQL insert. No code change, no redeploy, no restart |
| **Research and live share [`lib/compute/`](polybot/lib/compute/)** | Backtest and live cannot compute different features — the most common way quant systems die is structurally blocked here |
| **All thresholds SSOT in [`gates.py`](polybot/lib/gates.py)** | Risk controls live in one file; every number carries its measured provenance and rationale |
| **Feature pipeline split by data source** ([`research/features/`](research/features/)) | A new data source is a new module — it never touches the existing 292 features |
| **`migrations/` + a smoke test built on recorded responses** | Schema can evolve; changes have regression cover |

Different venue, different underlying, different fee structure — what you mostly change is
the ingest layer and the numbers in `gates.py`. **The skeleton is a general binary
prediction-market quant framework**, not something welded to Polymarket BTC 5m.

---

## There is edge left in the data — first come, first served

`polybot_live.db` is fully published. Its `factor_roster` holds 43 factors with their
complete status history. These are **still armed and carry positive out-of-sample net EV**:

| label | n | net EV (after fees) |
|---|---|---|
| `chgdn900_zs7d_dn` | 56 | **+12.83%** |
| `chgdn300_zs24h_up` | 44 | **+10.45%** |
| `R4` (`bn_taker_buy_ratio_pre_300>0.755`) | 569 | **+1.58%** |
| `bn_chg3600_rank_up` | 224 | +0.88% |
| `bn_tbr900_rank_dn` | 180 | +0.68% |

R4 has the thickest sample — 569 out-of-sample trades, still positive after the 7% taker
fee. It decayed noticeably toward the end, which is part of why I stopped, but **the signal
is genuinely there in the data**.

The top two show double-digit net EV but thin samples (n=56 / n=44) — nowhere near enough to
conclude anything. **They are public now. Whoever picks them up gets to find out.**

> These are not backtest curves. They are trade-by-trade out-of-sample paper fills, each
> with its entry price, order-book snapshot and settlement outcome. 3,560 rows in
> `paper_trade_5m_binary` — go check them yourself.

---

## Problems already solved for you

If you are building something similar, each of these cost me time or money.

### PM `/trades` caps at 4,000 records — and truncates **silently**

Early ingest used `limit=500` across 3 offsets and assumed it had everything. Probing the
real boundaries showed: `limit≤1000` is enforced **silently** (pass 5000, get no error and
1000 rows), `offset≤3000` is hard — so a single `cid` tops out at 4,000 trades.

Consequence: **early-candle fills were missing for 98.6% of markets**, biasing every
backtest entry-price estimate.

Fix: [`ingest_pm_trades_v3_4kcap_backfill_20260523.py`](research/ingestion/ingest_pm_trades_v3_4kcap_backfill_20260523.py).
**Probe pagination limits empirically — trust neither the docs nor the absence of an error.**

### Guessing rate limits wastes your entire budget

Without checking the docs I throttled to 1.3 req/s. Measured reality: data-api allows
**200 req/10s**, CLOB `/prices-history` allows **1000 req/10s** — I was using 0.13% of the
quota and my ingest ran two orders of magnitude slower than necessary.

The official docs ship an `llms.txt` index with rate limits on their own page. **This applies
even if you use an SDK — rate limits belong to the endpoint, not the client.**

### The documented enum is not what the API returns

Docs say `GET /data/order/{id}` returns `status: "ORDER_STATUS_MATCHED"`. It actually returns
`"MATCHED"`, no prefix. Result: 11 filled orders sat in `pending` forever, $11 locked in
escrow, wallet starved.

**For anything touching money, trust recorded responses over documentation. Docs don't
replace probing, and probing doesn't replace docs — do both.**

### `success: true` does not mean filled

A FOK (Fill-Or-Kill) order can return `success: true` together with `status: "delayed"` —
meaning **the money is already committed**, matching just happens asynchronously. If you only
record on `matched`, that spend falls off your books.

**Any `success: true` means money may already be gone. Persist it.**

### Reserve before you execute

All of the bugs above share one root cause: assuming external API behaviour without verifying
it. The architectural fix is to weld the ordering:

1. Write the intent to the DB first (`status='pending'`)
2. Call the external API
3. Update from the result
4. Clean up the reservation on failure

Then a mid-operation crash still leaves dedup and state tracking without a blind spot.
**Code that moves real money must be safe under *unknown* states, not just known ones —
mock tests can only validate logic you already knew about.**

### No third-party wrapper SDKs

Official SDK only; public read endpoints called directly with `httpx`. **No "convenience"
wrappers**, regardless of star count.

There are multiple reports on X and Reddit of third-party Polymarket wrappers shipping
backdoors, exfiltrating private keys, and silently rewriting order parameters. Writes —
ordering, cancelling, withdrawing — go through the official SDK, period.

### Factor admission: reject by default, the burden of proof is on the factor

Thresholds are centralised in [`polybot/lib/gates.py`](polybot/lib/gates.py), applied
uniformly, **never tuned per factor**:

| Gate | Value | Meaning |
|---|---|---|
| `PAPER_TO_LIVE_NET_EV` | 0.05 | net EV point estimate must clear 5% |
| `PAPER_TO_LIVE_T_STAT` | 1.65 | t-statistic on net EV |
| `PAPER_TO_LIVE_CAP_N` | 800 | kill if it hasn't graduated by n=800 |
| `PAPER_TO_LIVE_CAP_WEEKS` | 10 | kill on wall-clock too |
| `FACTOR_DEDUP_JACCARD_MAX` | 0.75 | reject factors overlapping an armed one |

27 of 43 factors were killed this way. **An edge that cannot be proven within a feasible
sample size gets killed even if it is real** — an edge that slow is worthless against a 7%
fee.

---

## Architecture

```
polybot/              production bot (VPS + systemd daemon)
  lib/gates.py        ← SSOT for every threshold. start reading here
  lib/expr_eval_v1.py factor expression evaluator — factors are data, not code
  lib/compute/        computation shared by research and live, so both agree
  runtime/            scanner, PM/Binance clients, websocket, execution, redemption
research/
  features/           feature engineering pipeline → features.parquet
  ingestion/          historical ingest (Binance klines, PM trades)
  mine/metrics.py     statistics shared by every scorecard
docs/                 methodology notes (Chinese)
tests/                including a smoke test built on recorded responses
```

## Why I stopped

Polymarket's 7% taker fee is the highest tier anywhere. Under that cost structure,
compounding a small high-frequency edge would require per-trade costs near zero and volume in
the tens of millions — structurally unavailable to small capital. Once R4's decay accelerated,
I called it.

**But the code, the data and the factors stay here.** Fee structures change, venues change,
and whoever picks this up may not be working under my constraints.

---

## Data

Shipped separately due to size:

| Dataset | Contents |
|---|---|
| `pm_btc5m.db` | 42,224 markets, `ep_panel` (40 intra-candle price columns), 215,388 Binance klines, funding rates / open interest / long-short ratios |
| `features.parquet` | 42,224 rows × 292 features |
| `polybot_live.db` | `factor_roster` (43 factors + status), `factor_log` (why each was killed), 3,560 paper fills in full detail |

**`ep_panel` cannot be collected again** — it is derived from Polymarket's `/trades` lookback
window, and that window has closed. The intra-candle price paths for these 42,224 markets
cannot be rebuilt from the API today.

**Download:** bundled in [`release/`](release/) via Git LFS, 200MB total.

```bash
git lfs install
git clone https://github.com/Bwijn/polymarket-btc-5m-quant.git
cd polymarket-btc-5m-quant/release
zstd -d pm_btc5m.db.zst polybot_live.db.zst roster.db.zst
tar xf research_data.tar
```

## Full build log

<!-- TODO: blog link -->
The whole thing, from zero to shutdown _(coming)_

## Contact

Factors, data, pitfalls, and whatever anyone turns up validating the remaining signals:

**Telegram: [@APPSMATRIXCHAT](https://t.me/APPSMATRIXCHAT)**

<img src="assets/tg_group.png" alt="Telegram @APPSMATRIXCHAT" width="220">

Those positive-EV factors with thin samples — if you get a result, come tell us.

---

None of this is financial advice. Third-party market data is bundled for research use;
re-ingest from source for anything else.
