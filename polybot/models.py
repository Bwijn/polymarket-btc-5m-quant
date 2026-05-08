"""DB models for binary 5m paper trade scanner.

Coexists with old copy-trade tables (paper_trades, real_trades) in same
polybot.db. New table only.
"""
from enum import Enum
from sqlmodel import SQLModel, Field


class TradeStatus(str, Enum):
    open = "open"
    settled = "settled"
    error = "error"        # ingest / settle failure; do not re-process


class Direction(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


class PaperTrade5mBinary(SQLModel, table=True):
    """One row per triggered candle. paper_trade_5m_binary.

    entry_price = price actually paid for our direction's token at cs+entry_offset_s.
        UP-bet  → entry_price = p_up_at_entry
        DOWN-bet → entry_price = 1 - p_up_at_entry  (binary complement)
    pnl_pct (after settle):
        win  → (1.0 - entry_price) / entry_price
        loss → -1.0  (lose entire stake)
    Friction NOT applied here — recorded raw; deflate at report time.
    """
    __tablename__ = "paper_trade_5m_binary"

    id: int | None = Field(default=None, primary_key=True)

    hypothesis: str            # 'H5', 'H6', ...
    expr: str                  # trigger expression text (audit)
    direction: Direction

    slug: str                  # btc-updown-5m-{cs}
    market_id: str             # conditionId
    up_token: str
    down_token: str

    candle_start: int          # cs (UTC unix, mod 300 == 0)
    entry_offset_s: int        # H5=60, H6=120

    p_up_at_entry: float       # UP-token price at cs+entry_offset_s (carry-forward)
    entry_price: float         # our side's price (UP→p_up; DOWN→1-p_up)
    size_usd: float            # nominal stake recorded at trigger time
    trigger_features: str      # JSON: {base_col: value, ...} actually evaluated

    status: TradeStatus = TradeStatus.open
    up_won: int | None = None         # 0/1 once event closed
    pnl_pct: float | None = None      # ratio, not %
    pnl_usd: float | None = None      # pnl_pct * size_usd

    opened_at: int                    # unix at trigger
    settled_at: int | None = None
    error_msg: str | None = None      # populated when status=error
