"""DB models for binary 5m paper trade scanner.

Coexists with old copy-trade tables in same polybot.db (paper_trades, real_trades).

Naming conventions:
  - timestamps:  _s suffix = unix seconds UTC, _ms suffix = unix milliseconds UTC
  - prices:      explicit _up/_dn for orderbook (both sides recorded, no ambiguity)
                 entry_price_*  / pnl_*  are direction-aware (自带"我方"含义),
                 read with `direction` column to interpret which side
  - pnl:         _ratio  (e.g. 0.05 = 5%, NOT 5 = 5%);  _usd  in dollars
"""
from enum import Enum
from sqlmodel import SQLModel, Field


class TradeStatus(str, Enum):
    open = "open"
    settled = "settled"
    error = "error"


class Direction(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


class PaperTrade5mBinary(SQLModel, table=True):
    """One row per triggered candle.

    PnL semantics for our side:
        win  → entry_price → 1.0   pnl_ratio = (1-entry)/entry
        loss → entry_price → 0.0   pnl_ratio = -1.0
    pnl_ratio_paper / pnl_usd_paper are GROSS (pre-fee), kept gross so drift
    vs backtest stays apples-to-apples. fee_usd + *_net columns hold the
    fee-deducted view; friction algo SSOT = polybot/lib/friction.py.
    """
    __tablename__ = "paper_trade_5m_binary"

    id: int | None = Field(default=None, primary_key=True)

    hypothesis: str
    expr: str
    direction: Direction

    slug: str
    market_id: str
    up_token: str
    down_token: str

    # ---- timing (UTC unix seconds) ----
    candle_start_s: int
    entry_offset_s: int

    # ---- backtest side: from CLOB /prices-history fidelity=1, carry-forward ----
    p_up_at_entry: float                      # UP token carry-forward at cs+entry_offset_s
    entry_price_backtest: float               # our side; UP→p_up, DOWN→1-p_up
    trigger_features: str                     # JSON snapshot of evaluated features

    # ---- paper side: ws CLOB book channel, both sides recorded for arb_gap analysis ----
    book_bid_up: float | None = None
    book_ask_up: float | None = None
    book_bid_dn: float | None = None
    book_ask_dn: float | None = None
    book_ts_ms_up: int | None = None
    book_ts_ms_dn: int | None = None
    entry_price_paper: float | None = None    # direction-aware: UP→ask_up, DOWN→ask_dn

    size_usd: float

    # ---- settle ----
    status: TradeStatus = TradeStatus.open
    up_won: int | None = None                 # 0/1, UP token settled at 1
    pnl_ratio_backtest: float | None = None
    pnl_ratio_paper: float | None = None
    pnl_ratio_drift: float | None = None      # paper - backtest
    pnl_usd_backtest: float | None = None
    pnl_usd_paper: float | None = None

    # ---- net of PM taker fee (friction SSOT: polybot/lib/friction.py) ----
    fee_usd: float | None = None                    # size_usd * fee_ratio(entry_price_paper)
    pnl_usd_paper_net: float | None = None          # pnl_usd_paper - fee_usd
    pnl_ratio_paper_net: float | None = None        # pnl_ratio_paper - fee_ratio(entry_price_paper)

    # ---- live track: real order on PM CLOB (only when Strategy.live + LIVE_ENABLED) ----
    order_id: str | None = None
    real_status: str | None = None           # placing / matched / delayed / failed
    tx_hash: str | None = None
    real_size_usd: float | None = None       # makingAmount — USDC paid for shares (ex-fee)
    real_fill_price: float | None = None     # makingAmount / takingAmount
    real_shares: float | None = None         # takingAmount — held until redeem
    real_fee_usd: float | None = None        # real_size_usd * fee_ratio(real_fill_price)
    pnl_usd_live: float | None = None        # gross, at resolution
    pnl_usd_live_net: float | None = None    # pnl_usd_live - real_fee_usd
    pnl_ratio_live: float | None = None
    pnl_ratio_drift_live: float | None = None  # pnl_ratio_live - pnl_ratio_paper

    # NOTE: a `redeem_tx TEXT` column also exists on this table — added by
    # scratch/migrate_add_redeem_tx.py (Component 6). Holds the redeem tx hash
    # once a winning position is converted to pUSD, or 'external' if redeemed
    # outside the loop. Deliberately kept OUT of this model so the redeem
    # feature can never break the trading hot path.

    opened_at_s: int
    settled_at_s: int | None = None
    error_msg: str | None = None


class FeatureHistory(SQLModel, table=True):
    """Rolling buffer for stateful transforms (__zs24h / __zs7d / __rank24h).

    Each paper trigger eval writes the current cs's base feature values here.
    Transforms query past values from this table to compute z-score / rank.
    PK (feature_name, cs) makes upsert idempotent across restarts.

    Pruning: scanner periodically deletes rows older than 7d (keep enough for __zs7d).
    Warm-start: backfilled from features.parquet on first deploy via
    scratch/warmstart_feature_history.py.
    """
    __tablename__ = "feature_history"

    feature_name: str = Field(primary_key=True)
    cs: int = Field(primary_key=True)            # candle_start unix seconds
    value: float | None = None                   # NULL = NaN (data gap)
