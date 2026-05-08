"""Pydantic models for Polymarket CLOB API responses.

Schema source: https://docs.polymarket.com (API reference)
"""
from enum import Enum
from sqlmodel import SQLModel


class PostOrderStatus(str, Enum):
    """POST /order response status values."""
    MATCHED = "matched"
    DELAYED = "delayed"
    LIVE = "live"


class OrderStatus(str, Enum):
    """GET /data/order/{id} response status values.
    Docs say ORDER_STATUS_* prefix, but actual API returns without prefix.
    """
    MATCHED = "MATCHED"
    LIVE = "LIVE"
    CANCELED = "CANCELED"
    INVALID = "INVALID"
    CANCELED_RESOLVED = "CANCELED_MARKET_RESOLVED"


class PostOrderResponse(SQLModel):
    """POST /order response."""
    success: bool = False
    orderID: str = ""
    status: str = ""           # POST_MATCHED | POST_DELAYED | POST_LIVE
    makingAmount: str = ""     # USDC paid (6-dec fixed-math), empty when delayed
    takingAmount: str = ""     # shares received (6-dec fixed-math), empty when delayed
    transactionsHashes: list[str] = []
    tradeIDs: list[str] = []
    errorMsg: str = ""
    error: str = ""            # 4xx/5xx responses use this field


class OpenOrder(SQLModel):
    """GET /data/order/{id} response."""
    id: str = ""
    status: str = ""           # ORDER_MATCHED | ORDER_CANCELED | ...
    owner: str = ""
    maker_address: str = ""
    market: str = ""           # = conditionId
    asset_id: str = ""         # = tokenId
    side: str = ""             # BUY | SELL
    original_size: str = ""    # order size (6-dec fixed-math)
    size_matched: str = ""     # filled size (6-dec fixed-math)
    price: str = ""            # limit price
    outcome: str = ""
    expiration: str = ""
    order_type: str = ""       # GTC | FOK | GTD | FAK
    created_at: int = 0
    associate_trades: list[str] = []
