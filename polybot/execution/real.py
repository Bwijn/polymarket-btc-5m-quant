import logging
import os
import time
from sqlmodel import Session, create_engine, select, SQLModel
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, MarketOrderArgs, OrderType
from config import REAL_TRADE_SIZE, PRICE_RANGE, CLOB_API, FUNDER_ADDRESS, REAL_COPY_WALLETS
from ingestion.api import get_last_trade_price, get_market_resolution
from models.trade import RealTrade, TradeStatus
from models.clob import PostOrderResponse, OpenOrder, PostOrderStatus, OrderStatus

log = logging.getLogger("polybot.real")


class RealExecutor:
    def __init__(self, db_path: str):
        self.engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(self.engine)
        self.client = self._init_client()
        log.info(f"ready, ${REAL_TRADE_SIZE}/trade")

    def _init_client(self) -> ClobClient:
        creds = ApiCreds(
            api_key=os.environ["POLY_API_KEY"],
            api_secret=os.environ["POLY_API_SECRET"],
            api_passphrase=os.environ["POLY_API_PASSPHRASE"],
        )
        return ClobClient(
            CLOB_API,
            key=os.environ["PRIVATE_KEY"],
            chain_id=137,
            creds=creds,
            signature_type=1,
            funder=FUNDER_ADDRESS,
        )

    def get_open_trades(self) -> list[RealTrade]:
        with Session(self.engine) as s:
            return list(s.exec(select(RealTrade).where(RealTrade.status == TradeStatus.open)).all())

    def get_all_trades(self) -> list[RealTrade]:
        with Session(self.engine) as s:
            return list(s.exec(select(RealTrade).order_by(RealTrade.opened_at.desc())).all())

    def _has_active_trade(self, market_id: str, wallet: str) -> bool:
        with Session(self.engine) as s:
            row = s.exec(
                select(RealTrade)
                .where(RealTrade.market_id == market_id)
                .where(RealTrade.source_wallet == wallet)
                .where(RealTrade.status.in_([TradeStatus.open, TradeStatus.pending]))
            ).first()
            return row is not None

    async def copy_trade(self, trade: dict):
        if trade["wallet"] not in REAL_COPY_WALLETS:
            return

        if self._has_active_trade(trade["market_id"], trade["wallet"]):
            return

        cur_price = await get_last_trade_price(trade["token_id"])
        if cur_price is None:
            log.warning(f"No price for {trade['title']}, skip")
            return

        if not (PRICE_RANGE[0] <= cur_price <= PRICE_RANGE[1]):
            log.info(f"Price {cur_price:.2f} out of range, skip")
            return

        drift = abs(cur_price - trade["price"]) / trade["price"]
        if drift > 0.10:
            log.info(f"Price drifted {drift:.0%} from signal, skip")
            return

        # Reserve DB slot BEFORE placing order — dedup is never blind
        row = RealTrade(
            market_id=trade["market_id"], token_id=trade["token_id"],
            title=trade["title"], side="BUY", entry_price=cur_price,
            size=REAL_TRADE_SIZE, source_wallet=trade["wallet"],
            status=TradeStatus.pending, opened_at=int(time.time()),
        )
        with Session(self.engine) as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        tid = row.id

        try:
            order_args = MarketOrderArgs(
                amount=float(REAL_TRADE_SIZE),
                price=min(0.99, round(cur_price + 0.01, 2)),
                side="BUY",
                token_id=trade["token_id"],
            )
            signed_order = self.client.create_market_order(order_args)
            raw = self.client.post_order(signed_order, OrderType.FOK)
            resp = PostOrderResponse.model_validate(raw)

            if not resp.success:
                log.error(f"Order rejected: {resp.errorMsg or resp.error}")
                self._delete(tid)
                return

            if resp.status == PostOrderStatus.DELAYED:
                with Session(self.engine) as s:
                    t = s.get(RealTrade, tid)
                    t.order_id = resp.orderID
                    s.commit()
                log.warning(f"#{tid}: delayed, pending until resolved | {trade['title']}")
                return

            if resp.status not in (PostOrderStatus.MATCHED, PostOrderStatus.LIVE):
                log.error(f"#{tid}: unknown status '{resp.status}', deleting | resp={raw}")
                self._delete(tid)
                return

            # BUY FOK matched: makingAmount=USDC paid, takingAmount=shares received (6-dec)
            making = float(resp.makingAmount or 0)
            taking = float(resp.takingAmount or 0)
            fill_price = making / taking if taking > 0 else cur_price
            if taking == 0:
                log.warning(f"#{tid}: no fill amounts, fallback to cur_price")

            with Session(self.engine) as s:
                t = s.get(RealTrade, tid)
                t.status = TradeStatus.open
                t.order_id = resp.orderID
                t.entry_price = fill_price
                s.commit()

            slip = (fill_price - cur_price) / cur_price if cur_price else 0
            log.info(f"#{tid}: BUY ${REAL_TRADE_SIZE} @ {fill_price:.3f} (cur={cur_price:.3f}, slip={slip:+.1%}) | {trade['wallet'][:10]}.. | {trade['title']}")

        except Exception as e:
            log.error(f"Order failed: {e}")
            self._delete(tid)

    async def check_pending(self):
        """Resolve delayed orders: CLOB gone → delete, filled → open."""
        with Session(self.engine) as s:
            rows = s.exec(
                select(RealTrade)
                .where(RealTrade.status == TradeStatus.pending)
                .where(RealTrade.order_id.isnot(None))
            ).all()

        for r in rows:
            try:
                raw = self.client.get_order(r.order_id)
            except Exception as e:
                log.error(f"#{r.id}: check_pending error: {e}")
                continue

            if raw is None:
                self._delete(r.id)
                log.info(f"#{r.id}: delayed order expired, deleted | {r.title}")
                continue

            order = OpenOrder.model_validate(raw)

            if order.status == OrderStatus.MATCHED:
                # OpenOrder has no USDC amount field; use limit price as fill_price.
                # FOK fills at <= limit, so this may overstate entry by <=1 tick.
                # For precise VWAP, query /data/trades?taker_order_id=... or subscribe WS user channel.
                fill_price = float(order.price or 0)
                with Session(self.engine) as s:
                    t = s.get(RealTrade, r.id)
                    t.status = TradeStatus.open
                    t.entry_price = fill_price
                    s.commit()
                log.info(f"#{r.id}: delayed order filled @ {fill_price:.3f} | {r.title}")
            elif order.status in (OrderStatus.CANCELED, OrderStatus.INVALID, OrderStatus.CANCELED_RESOLVED):
                self._delete(r.id)
                log.info(f"#{r.id}: order {order.status}, deleted | {r.title}")
            else:
                log.info(f"#{r.id}: still pending ({order.status}) | {r.title}")

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
            t = s.get(RealTrade, trade_id)
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

    def _delete(self, trade_id: int):
        with Session(self.engine) as s:
            t = s.get(RealTrade, trade_id)
            if t:
                s.delete(t)
                s.commit()
