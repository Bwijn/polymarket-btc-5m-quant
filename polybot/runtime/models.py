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
    Friction NOT applied here — recorded raw; deflate at report time.
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
