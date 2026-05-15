"""Paper-phase config. Live config (API key/private key/funder) loaded later."""
import os
from pathlib import Path

_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ---- PM hosts (read endpoints) ---------------------------------------------
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

# ---- runtime ----------------------------------------------------------------
DB_FILE   = "polybot.db"
LOG_FILE  = "polybot.log"

# ---- paper phase ------------------------------------------------------------
# Timer-driven schedule. Each (strategy, candle) gets one asyncio task that
# precisely sleeps until cs+entry_offset+ENTRY_LAG_S. No polling for trigger
# evaluation — wakeup is wall-clock-aligned to the strategy's entry moment.
ENTRY_LAG_S       = 0         # snap = wake + eval_RTT (HK→PM ~22ms p50). 0 → snap lands ~cs+offset, aligns with mining reference. Was 1 (legacy "wait for 1-min sample" — proven unnecessary).
SETTLE_LAG_S      = 60        # wait past cs+300 before trying settle (resolution lag)
SCHEDULE_REFRESH_S = 60       # rescan upcoming candles + register schedule tasks
SETTLE_POLL_S     = 30        # poll open rows for settle

# size_usd recorded per paper trade (Kelly applied per-strategy in scanner —
# this is fallback / nominal). Real wallet = $34.13, 5% Kelly → $1.71.
PAPER_SIZE_USD    = 1.70

# Asset (binary 5m). Single asset for now.
ASSET_PREFIX      = "btc-updown-5m-"     # slug = ASSET_PREFIX + str(cs)
