import httpx
import logging
from config import GAMMA_API, CLOB_API, DATA_API

log = logging.getLogger("polybot")

client = httpx.AsyncClient(timeout=30)


async def fetch_user_activity(address: str, limit: int = 20) -> list[dict]:
    r = await client.get(f"{DATA_API}/activity", params={"user": address, "limit": limit, "offset": 0})
    if r.status_code != 200:
        return []
    return r.json()


async def fetch_user_positions(address: str, limit: int = 500) -> list[dict]:
    r = await client.get(f"{DATA_API}/positions", params={"user": address, "limit": limit, "offset": 0})
    if r.status_code != 200:
        return []
    return r.json()


async def get_active_markets(limit: int = 100) -> list[dict]:
    r = await client.get(f"{GAMMA_API}/markets", params={"limit": limit, "active": "true"})
    r.raise_for_status()
    return r.json()


async def get_last_trade_price(token_id: str) -> float | None:
    r = await client.get(f"{CLOB_API}/last-trade-price", params={"token_id": token_id})
    if r.status_code != 200:
        return None
    data = r.json()
    return float(data.get("price", 0))


async def get_market_resolution(condition_id: str) -> dict | None:
    r = await client.get(f"{CLOB_API}/markets/{condition_id}")
    if r.status_code != 200:
        return None
    data = r.json()
    if not data.get("closed"):
        return None
    winners = {t["token_id"]: t.get("winner", False) for t in data.get("tokens", [])}
    return winners


async def get_spread(token_id: str) -> float | None:
    r = await client.get(f"{CLOB_API}/book", params={"token_id": token_id})
    if r.status_code != 200:
        return None
    book = r.json()
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    if not bids or not asks:
        return None
    return float(asks[0]["price"]) - float(bids[0]["price"])
