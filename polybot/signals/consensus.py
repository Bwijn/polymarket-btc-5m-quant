import logging
import time
from collections import defaultdict
from db.store import Store
from config import (
    CONSENSUS_THRESHOLD, MIN_BASKET_AGREEMENT,
    SIGNAL_WINDOW_HOURS, PRICE_RANGE, MAX_SPREAD
)
from ingestion.api import get_spread, get_last_trade_price

log = logging.getLogger("polybot")


class ConsensusEngine:
    def __init__(self, store: Store):
        self.store = store
        self._last_signals: dict[str, int] = {}

    async def scan(self) -> list[dict]:
        """Scan recent smart wallet trades for consensus signals."""
        window = SIGNAL_WINDOW_HOURS * 3600
        wallets = self.store.get_smart_wallets()
        wallet_set = {w["address"].lower() for w in wallets}
        basket_size = len(wallet_set)
        if basket_size == 0:
            return []

        # group recent trades by market
        market_votes: dict[str, dict[str, set]] = defaultdict(lambda: {"BUY": set(), "SELL": set()})

        for w in wallets:
            addr = w["address"].lower()
            # get trades from all markets for this wallet
            cutoff = int(time.time()) - window
            rows = self.store.conn.execute(
                "SELECT * FROM wallet_trades WHERE wallet=? AND timestamp>=?",
                (addr, cutoff)).fetchall()
            for row in rows:
                market_id = row["market_id"]
                side = row["side"].upper()
                if side in ("BUY", "SELL"):
                    market_votes[market_id][side].add(addr)

        signals = []
        for market_id, votes in market_votes.items():
            for side in ("BUY", "SELL"):
                agreeing = votes[side]
                count = len(agreeing)
                if count < MIN_BASKET_AGREEMENT:
                    continue

                consensus_pct = count / basket_size
                if consensus_pct < CONSENSUS_THRESHOLD:
                    continue

                # deduplicate: don't fire same signal within window
                sig_key = f"{market_id}:{side}"
                if sig_key in self._last_signals:
                    if time.time() - self._last_signals[sig_key] < window:
                        continue

                # get current price
                # use first token_id from trades for this market
                sample_row = self.store.conn.execute(
                    "SELECT token_id FROM wallet_trades WHERE market_id=? LIMIT 1",
                    (market_id,)).fetchone()
                if not sample_row:
                    continue
                token_id = sample_row["token_id"]

                price = await get_last_trade_price(token_id)
                if price is None:
                    continue
                if not (PRICE_RANGE[0] <= price <= PRICE_RANGE[1]):
                    continue

                spread = await get_spread(token_id)
                if spread is not None and spread > MAX_SPREAD:
                    log.info(f"Skip {market_id[:16]}.. spread={spread:.3f} > {MAX_SPREAD}")
                    continue

                signal = {
                    "market_id": market_id,
                    "token_id": token_id,
                    "side": side,
                    "consensus_pct": consensus_pct,
                    "agreeing_count": count,
                    "agreeing_wallets": list(agreeing),
                    "price": price,
                }
                signals.append(signal)
                self._last_signals[sig_key] = int(time.time())
                log.info(f"SIGNAL: {side} {market_id[:16]}.. consensus={consensus_pct:.0%} ({count}/{basket_size}) @ {price:.2f}")

        return signals
