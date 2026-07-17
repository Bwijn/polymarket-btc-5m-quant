"""DB models for binary 5m paper trade scanner.

Coexists with old copy-trade tables in same polybot.db (paper_trades, real_trades).

Naming conventions:
  - line suffix: _backtest / _paper / _live tags which of the 3 tracks a column
                 belongs to; line-agnostic market facts (condition_id, up_won,
                 status) carry no suffix
  - timestamps:  _s = unix seconds UTC, _ms = unix milliseconds UTC
  - prices:      _up/_dn for orderbook (both sides recorded, no ambiguity);
                 entry_price_* / pnl_* are direction-aware (自带"我方"含义),
                 read with `direction` to interpret which side
  - pnl:         _ratio (e.g. 0.05 = 5%, NOT 5 = 5%);  _usd in dollars
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
    """One row per triggered candle — 2 parallel tracks: paper / live.

    bt (backtest) track removed 2026-05-26: bt ep was obsolete fidelity=1 mid-price
    method, drift vs paper meaningless. Now derived on-demand via PM /trades endpoint
    in scratch/research/compute_drift.py (trades = reproducible truth, no schema churn).

    PnL semantics (our side):
        win  → entry → 1.0   pnl_ratio = (1-entry)/entry
        loss → entry → 0.0   pnl_ratio = -1.0
    *_ratio = fraction (0.05 = 5%);  *_usd = dollars. Gross columns are pre-fee;
    *_net columns subtract PM taker fee (friction SSOT: polybot/lib/friction.py).
    """
    __tablename__ = "paper_trade_5m_binary"

    id: int | None = Field(default=None, primary_key=True)

    # ---- strategy ----
    expr: str             # sole strategy identity (dedup key + dashboard join key)
    direction: Direction

    # ---- market (line-agnostic facts) ----
    slug: str
    condition_id: str                         # CTF conditionId hash
    up_token: str
    down_token: str

    # ---- timing (UTC unix seconds) ----
    candle_start_s: int
    entry_offset_s: int
    opened_at_s: int
    settled_at_s: int | None = None

    # ---- bt track REMOVED 2026-05-26 — derived on-demand via PM /trades endpoint
    # (reproducible truth). See scratch/research/compute_drift.py.
    # Dropped cols: p_up_at_entry_backtest, entry_price_backtest, trigger_features_backtest,
    #               pnl_ratio_backtest, pnl_usd_backtest, pnl_ratio_drift_paper_backtest.

    # ---- paper track: ws CLOB book channel, both sides recorded for arb_gap analysis ----
    book_bid_up: float | None = None
    book_ask_up: float | None = None
    book_bid_dn: float | None = None
    book_ask_dn: float | None = None
    book_ts_ms_up: int | None = None
    book_ts_ms_dn: int | None = None
    entry_price_paper: float | None = None    # direction-aware: UP→ask_up, DOWN→ask_dn
    pnl_ratio_paper: float | None = None
    pnl_ratio_paper_net: float | None = None  # ratio SSOT — the only paper metric promote reads

    # ---- live track: real order on PM CLOB (only when Strategy.live + LIVE_ENABLED) ----
    order_id: str | None = None
    order_status_live: str | None = None      # order lifecycle: placing / matched / delayed / failed
    tx_hash_live: str | None = None           # the BUY tx hash
    entry_price_live: float | None = None     # makingAmount / takingAmount
    size_usd_live: float | None = None        # makingAmount — USDC paid for shares (ex-fee)
    shares_live: float | None = None          # takingAmount — held until redeem
    fee_usd_live: float | None = None         # size_usd_live * fee_ratio(entry_price_live)
    pnl_ratio_live: float | None = None
    pnl_ratio_live_net: float | None = None
    pnl_usd_live: float | None = None         # gross, at resolution
    pnl_usd_live_net: float | None = None     # pnl_usd_live - fee_usd_live
    redeem_tx: str | None = None              # redeem tx hash, or 'external'; unsuffixed —
    #                                           redeem.py depends on this name, redeem is live-only

    # ---- cross-track drift  (drift_A_B = A − B). bt-drift removed (computed in script).
    pnl_ratio_drift_live_paper: float | None = None

    # ---- outcome (line-agnostic) ----
    status: TradeStatus = TradeStatus.open
    up_won: int | None = None                 # 0/1, UP token settled at 1
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
