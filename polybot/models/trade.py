"""DB table models for copy trading."""
from enum import Enum
from sqlmodel import SQLModel, Field


class TradeStatus(str, Enum):
    pending = "pending"
    open = "open"
    settled = "settled"


class RealTrade(SQLModel, table=True):
    __tablename__ = "real_trades"
    id: int | None = Field(default=None, primary_key=True)
    market_id: str
    token_id: str
    title: str
    side: str
    entry_price: float
    size: float
    exit_price: float | None = None
    pnl: float | None = None
    status: TradeStatus = TradeStatus.pending
    source_wallet: str
    order_id: str | None = None
    opened_at: int
    closed_at: int | None = None


class PaperTrade(SQLModel, table=True):
    __tablename__ = "paper_trades"
    id: int | None = Field(default=None, primary_key=True)
    market_id: str
    token_id: str
    title: str
    side: str
    entry_price: float
    size: float
    exit_price: float | None = None
    pnl: float | None = None
    status: TradeStatus = TradeStatus.open
    source_wallet: str
    opened_at: int
    closed_at: int | None = None
