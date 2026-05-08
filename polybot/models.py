"""DB models for binary 5m paper trade scanner.

Coexists with old copy-trade tables in same polybot.db (paper_trades, real_trades).

Design rationale: paper trade's deliverable is *drift between backtest and
real orderbook*. Each row records BOTH:
  - backtest entry: 1 - p_up_at_entry  (carry-forward from prices-history fid=1)
  - paper entry:    book_ask           (live ws best_ask of our side's token)
At settle we compute pnl_pct twice; their difference = drift, the metric for
go/no-go on live promotion.
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
        win  → entry_price → 1.0 (token settles at 1)   pnl_pct = (1-entry)/entry
        loss → entry_price → 0.0 (token settles at 0)   pnl_pct = -1.0
    Friction NOT applied — recorded raw; deflate at report time.
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

    candle_start: int
    entry_offset_s: int

    # backtest-side: from CLOB /prices-history fidelity=1, carry-forward
    p_up_at_entry: float                      # UP price at cs+entry_offset_s
    entry_price_backtest: float               # our side; UP→p_up, DOWN→1-p_up
    trigger_features: str                     # JSON snapshot of evaluated features

    # paper-side: from ws CLOB market channel, real orderbook at trigger moment
    book_bid: float | None = None             # our token's best_bid
    book_ask: float | None = None             # our token's best_ask
    book_mid: float | None = None             # (bid+ask)/2
    book_ts_ms: int | None = None             # ms timestamp of the ws update used
    entry_price_paper: float | None = None    # what we'd actually pay = book_ask
                                              # (taker BUY crosses the spread)

    size_usd: float

    # ---- settle ----
    status: TradeStatus = TradeStatus.open
    up_won: int | None = None
    pnl_pct_backtest: float | None = None
    pnl_pct_paper: float | None = None
    pnl_drift_pct: float | None = None        # paper - backtest
    pnl_usd_backtest: float | None = None
    pnl_usd_paper: float | None = None

    opened_at: int
    settled_at: int | None = None
    error_msg: str | None = None
