import asyncio
import logging
import time
from ingestion.api import fetch_user_activity
from config import COPY_WALLETS

log = logging.getLogger("polybot")


class WalletWatcher:
    def __init__(self):
        self._last_tx: dict[str, str] = {}

    async def poll(self) -> list[dict]:
        new_trades = []
        for addr in COPY_WALLETS:
            try:
                trades = await self._poll_wallet(addr)
                new_trades.extend(trades)
            except Exception as e:
                log.error(f"Error polling {addr[:10]}.. : {e}")
            await asyncio.sleep(1)
        return new_trades

    async def _poll_wallet(self, addr: str) -> list[dict]:
        activities = await fetch_user_activity(addr, limit=5)
        if not activities:
            return []

        latest = activities[0]
        prev_tx = self._last_tx.get(addr)
        latest_tx = latest.get("transactionHash", "")

        if prev_tx == latest_tx:
            return []

        self._last_tx[addr] = latest_tx

        if prev_tx is None:
            return []  # first poll, store baseline

        # collect all new BUY trades
        result = []
        for t in activities:
            if t.get("transactionHash") == prev_tx:
                break
            if t.get("side", "").upper() != "BUY":
                continue
            if t.get("type") != "TRADE":
                continue

            result.append({
                "wallet": addr,
                "market_id": t.get("conditionId", ""),
                "token_id": t.get("asset", ""),
                "title": t.get("title", ""),
                "price": float(t.get("price", 0)),
                "size": float(t.get("usdcSize", 0)),
                "timestamp": int(t.get("timestamp", time.time())),
            })

        for r in result:
            log.info(f"NEW | {addr[:10]}.. | BUY ${r['size']:.0f} @ {r['price']:.2f} | {r['title']}")

        return result
