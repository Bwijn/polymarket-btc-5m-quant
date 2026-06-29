"""Scanner config — paper + live execution. .env auto-loaded into os.environ."""
import os
from pathlib import Path

# .env lives at the polybot/ root (one level up from runtime/).
for _env in (Path(__file__).parent.parent / ".env", Path(__file__).parent / ".env"):
    if _env.exists():
        for line in _env.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
        break

# ---- PM hosts (read endpoints) ---------------------------------------------
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

# ---- runtime ----------------------------------------------------------------
DB_FILE   = "polybot.db"
LOG_FILE  = "polybot.log"

# ---- paper phase ------------------------------------------------------------
# Timer-driven schedule. Each (strategy, candle) gets one asyncio task that
# precisely sleeps until cs+entry_offset. No polling for trigger evaluation —
# wakeup is wall-clock-aligned to the strategy's entry moment (= mining reference).
SETTLE_LAG_S      = 60        # wait past cs+300 before trying settle (resolution lag)
SCHEDULE_REFRESH_S = 60       # rescan upcoming candles + register schedule tasks
SETTLE_POLL_S     = 30        # poll open rows for settle

# size_usd_intended recorded per paper trade (Kelly applied per-strategy in scanner —
# this is fallback / nominal). Real wallet = $34.13, 5% Kelly → $1.71.
PAPER_SIZE_USD    = 1.70

# Asset (binary 5m). Single asset for now.
ASSET_PREFIX      = "btc-updown-5m-"     # slug = ASSET_PREFIX + str(cs)

# ---- live execution ---------------------------------------------------------
# LIVE_ENABLED is the global kill switch. False → scanner is pure paper: no
# LiveExecutor init, private key never touched, no CLOB write API. Flip True
# + redeploy (executor inits + L2 auth-checks once at startup) to arm live.
# Per-strategy gate: Strategy.live must ALSO be True — both required to place.
LIVE_ENABLED = True
CHAIN_ID     = 137                                          # Polygon
SIG_TYPE     = 1                                            # POLY_PROXY
FUNDER       = "0x606970B1b66993A8E36C6CD41c1823317152f7ae" # proxy wallet (data-api verified)

# Per-trade size = wallet_usdc × BANKROLL_FRAC[label]. FIXED-FRACTION of bankroll —
# NOT Kelly: ignores edge/winrate, flat % per wager. (R4 full-Kelly≈26%; 5%≈0.2×,
# conservative for a marginal-CI first-live trial. Fractional-Kelly = future upgrade
# once real-money OOS tightens the edge estimate.) Fresh balance query per HIT →
# size auto-deflates as concurrent positions consume USDC.
BANKROLL_FRAC     = {"R4": 0.05}      # killed factors removed; arm via Strategy.live + deploy
LIVE_MIN_USD      = 1.0          # PM min order notional; skip live if size below
# Per-factor BUY slippage cap moved onto Strategy.slippage_cap (strategies.py) —
# a factor's execution knobs belong with the factor, not a label-keyed config dict.
