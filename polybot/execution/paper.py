import logging
import time
from sqlmodel import Session, create_engine, select, SQLModel
from config import TRADE_SIZE, PRICE_RANGE
from ingestion.api import get_market_resolution
from models.trade import PaperTrade, TradeStatus

log = logging.getLogger("polybot.paper")


class PaperExecutor:
    def __init__(self, db_path: str):
        self.engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(self.engine)

    def get_open_trades(self) -> list[PaperTrade]:
        with Session(self.engine) as s:
            return list(s.exec(select(PaperTrade).where(PaperTrade.status == TradeStatus.open)).all())

    def get_all_trades(self) -> list[PaperTrade]:
        with Session(self.engine) as s:
            return list(s.exec(select(PaperTrade).order_by(PaperTrade.opened_at.desc())).all())

    async def copy_trade(self, trade: dict):
        for t in self.get_open_trades():
            if t.market_id == trade["market_id"] and t.source_wallet == trade["wallet"]:
                return

        price = trade["price"]
        if not (PRICE_RANGE[0] <= price <= PRICE_RANGE[1]):
            log.info(f"Price {price:.2f} out of range, skip")
            return

        row = PaperTrade(
            market_id=trade["market_id"], token_id=trade["token_id"],
            title=trade["title"], side="BUY", entry_price=price,
            size=TRADE_SIZE, source_wallet=trade["wallet"],
            opened_at=int(time.time()),
        )
        with Session(self.engine) as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        log.info(f"#{row.id}: BUY ${TRADE_SIZE} @ {price:.2f} | {trade['wallet'][:10]}.. | {trade['title']}")

    async def check_settlements(self):
        checked = set()
        for t in self.get_open_trades():
            mid = t.market_id
            if not mid or mid in checked:
                continue
            checked.add(mid)
            winners = await get_market_resolution(mid)
            if winners is None:
                continue
            for trade in self.get_open_trades():
                if trade.market_id != mid:
                    continue
                won = winners.get(trade.token_id, False)
                self._settle(trade.id, 1.0 if won else 0.0)

    def _settle(self, trade_id: int, settle_price: float):
        with Session(self.engine) as s:
            t = s.get(PaperTrade, trade_id)
            if not t:
                return
            pnl = (settle_price - t.entry_price) / t.entry_price * t.size
            t.exit_price = settle_price
            t.pnl = pnl
            t.status = TradeStatus.settled
            t.closed_at = int(time.time())
            s.commit()
            result = "WIN" if pnl > 0 else "LOSS"
            log.info(f"SETTLED #{trade_id}: {result} ${pnl:+.2f} | entry={t.entry_price:.2f} -> {settle_price:.0f} | {t.title}")
