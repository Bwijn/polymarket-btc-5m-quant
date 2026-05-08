"""Paper-trade scanner for binary 5m BTC up/down markets.

Each TICK_S seconds:
  1. Trigger eval — for current + previous candle, for each ACTIVE strategy:
     If now ≥ cs + entry_offset_s + ENTRY_LAG_S and (strategy_id, cs) not yet
     recorded, fetch ticks via PM CLOB /prices-history, compute features,
     evaluate trigger expression. On hit, INSERT a paper_trade_5m_binary row.
  2. Settle — for each open row where cs+300+SETTLE_LAG_S ≤ now, fetch event
     via PM Gamma /events/slug, parse outcomePrices, compute pnl, UPDATE.

Trigger semantics 1:1 with mining: same expr_eval_v1.evaluate, same
entry_price.get_price_at carry-forward, same fidelity=1 prices-history.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from typing import Optional
from sqlmodel import Session, select, SQLModel, create_engine

from config import DB_FILE, TICK_S, ENTRY_LAG_S, SETTLE_LAG_S, PAPER_SIZE_USD
from models import PaperTrade5mBinary, TradeStatus, Direction
from strategies import ACTIVE, Strategy
from factor.expr_eval_v1 import evaluate
from factor.entry_price import get_price_at
from factor.features import compute_row
from pm_api import slug_for, fetch_event, parse_market, fetch_prices_history
from pm_ws import BookCache

log = logging.getLogger("polybot.scanner")

CANDLE_S = 300


def _engine(db_path: str):
    eng = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(eng)
    return eng


class Scanner:
    def __init__(self, db_path: str = DB_FILE, book_cache: BookCache | None = None):
        self.engine = _engine(db_path)
        self.book_cache = book_cache or BookCache()
        # In-memory de-dup of evaluated (strategy_id, cs) pairs. DB is SSOT for
        # hits; misses we keep ephemeral — process restart re-eval is harmless
        # because too-late candles are skipped by the time-window check.
        self._evaluated: set[tuple[str, int]] = set()

    def _seed_evaluated_from_db(self, cs_min: int) -> None:
        with Session(self.engine) as s:
            rows = s.exec(
                select(PaperTrade5mBinary.hypothesis, PaperTrade5mBinary.candle_start)
                .where(PaperTrade5mBinary.candle_start >= cs_min)
            ).all()
        for r in rows:
            self._evaluated.add((r[0], r[1]))

    # ---- trigger ------------------------------------------------------------
    async def eval_one(self, strat: Strategy, cs: int) -> None:
        slug = slug_for(cs)
        ev = await fetch_event(slug)
        if ev is None:
            log.info(f"[{strat.id}] cs={cs} event not found yet (slug={slug})")
            return
        m = parse_market(ev)
        if m is None:
            log.warning(f"[{strat.id}] cs={cs} market unparseable")
            return

        ticks = await fetch_prices_history(m['up_token'], cs, cs + strat.entry_offset_s, fidelity=1)
        if not ticks:
            log.info(f"[{strat.id}] cs={cs} no ticks yet")
            return

        df = compute_row(strat.expr, ticks, cs)
        hit = bool(evaluate(strat.expr, df).iloc[0])
        self._evaluated.add((strat.id, cs))
        if not hit:
            return

        p_up = get_price_at(ticks, strat.entry_offset_s, cs)
        if p_up is None:
            log.warning(f"[{strat.id}] cs={cs} HIT but p_up None — skip")
            return
        entry_price_backtest = p_up if strat.direction == 'UP' else 1.0 - p_up

        # paper-side: live orderbook of OUR side's token (we BUY = cross to ask)
        my_token = m['up_token'] if strat.direction == 'UP' else m['down_token']
        snap = self.book_cache.snapshot(my_token)
        book_bid = snap['best_bid'] if snap else None
        book_ask = snap['best_ask'] if snap else None
        book_mid = (book_bid + book_ask) / 2 if (book_bid is not None and book_ask is not None) else None
        book_ts_ms = snap['ts_ms'] if snap else None
        entry_price_paper = book_ask  # taker BUY pays ask

        row = PaperTrade5mBinary(
            hypothesis=strat.id,
            expr=strat.expr,
            direction=Direction(strat.direction),
            slug=slug,
            market_id=m['market_id'],
            up_token=m['up_token'],
            down_token=m['down_token'],
            candle_start=cs,
            entry_offset_s=strat.entry_offset_s,
            p_up_at_entry=p_up,
            entry_price_backtest=entry_price_backtest,
            book_bid=book_bid,
            book_ask=book_ask,
            book_mid=book_mid,
            book_ts_ms=book_ts_ms,
            entry_price_paper=entry_price_paper,
            size_usd=PAPER_SIZE_USD,
            trigger_features=df.iloc[0].to_json(),
            status=TradeStatus.open,
            opened_at=int(time.time()),
        )
        with Session(self.engine) as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        drift = (entry_price_paper - entry_price_backtest) if entry_price_paper else None
        log.info(f"[{strat.id}] HIT cs={cs} p_up={p_up:.3f} "
                 f"entry_bt={entry_price_backtest:.3f} entry_paper={entry_price_paper} "
                 f"drift={f'{drift:+.3f}' if drift is not None else 'n/a'} "
                 f"size=${PAPER_SIZE_USD:.2f} #{row.id}")

    # ---- settle -------------------------------------------------------------
    async def settle_one(self, row: PaperTrade5mBinary) -> None:
        ev = await fetch_event(row.slug)
        if ev is None:
            log.info(f"settle {row.id}: event not found")
            return
        m = parse_market(ev)
        if m is None or not m['closed'] or m['up_won'] is None:
            return  # not yet resolved

        my_won = (
            (row.direction == 'UP'   and m['up_won'] == 1) or
            (row.direction == 'DOWN' and m['up_won'] == 0)
        )
        # backtest pnl
        if my_won:
            pnl_pct_bt = (1.0 - row.entry_price_backtest) / row.entry_price_backtest
        else:
            pnl_pct_bt = -1.0
        pnl_usd_bt = pnl_pct_bt * row.size_usd

        # paper pnl (only if we captured book_ask at trigger)
        pnl_pct_paper = pnl_usd_paper = pnl_drift = None
        if row.entry_price_paper is not None and row.entry_price_paper > 0:
            if my_won:
                pnl_pct_paper = (1.0 - row.entry_price_paper) / row.entry_price_paper
            else:
                pnl_pct_paper = -1.0
            pnl_usd_paper = pnl_pct_paper * row.size_usd
            pnl_drift = pnl_pct_paper - pnl_pct_bt

        with Session(self.engine) as s:
            db_row = s.get(PaperTrade5mBinary, row.id)
            db_row.status = TradeStatus.settled
            db_row.up_won = m['up_won']
            db_row.pnl_pct_backtest = pnl_pct_bt
            db_row.pnl_pct_paper = pnl_pct_paper
            db_row.pnl_drift_pct = pnl_drift
            db_row.pnl_usd_backtest = pnl_usd_bt
            db_row.pnl_usd_paper = pnl_usd_paper
            db_row.settled_at = int(time.time())
            s.add(db_row)
            s.commit()

        outcome = "WIN" if my_won else "LOSS"
        paper_str = (f"paper={pnl_pct_paper:+.1%} drift={pnl_drift:+.1%}"
                     if pnl_pct_paper is not None else "paper=n/a")
        log.info(f"settle #{row.id} [{row.hypothesis}] {outcome} "
                 f"bt={pnl_pct_bt:+.1%} {paper_str} up_won={m['up_won']}")

    # ---- main tick ----------------------------------------------------------
    async def tick(self) -> None:
        now = int(time.time())
        cs_now  = (now // CANDLE_S) * CANDLE_S
        cs_prev = cs_now - CANDLE_S
        candles_to_check = [cs_prev, cs_now]

        for strat in ACTIVE:
            for cs in candles_to_check:
                if (strat.id, cs) in self._evaluated:
                    continue
                if now < cs + strat.entry_offset_s + ENTRY_LAG_S:
                    continue
                if now > cs + CANDLE_S + ENTRY_LAG_S:
                    self._evaluated.add((strat.id, cs))   # too late, never reconsider
                    continue
                try:
                    await self.eval_one(strat, cs)
                except Exception as e:
                    log.exception(f"[{strat.id}] cs={cs} eval error: {e}")

        # settle — old enough open rows
        cutoff = now - CANDLE_S - SETTLE_LAG_S
        with Session(self.engine) as s:
            open_rows = s.exec(
                select(PaperTrade5mBinary)
                .where(PaperTrade5mBinary.status == TradeStatus.open)
                .where(PaperTrade5mBinary.candle_start <= cutoff)
            ).all()
        for row in open_rows:
            try:
                await self.settle_one(row)
            except Exception as e:
                log.exception(f"settle #{row.id} error: {e}")

        # garbage-collect stale evaluated keys (older than 2 candles)
        gc_cutoff = cs_prev - CANDLE_S
        self._evaluated = {(h, cs) for (h, cs) in self._evaluated if cs >= gc_cutoff}

    async def run_forever(self) -> None:
        # Seed with hits already recorded to avoid duplicate INSERT after restart
        cs_now = (int(time.time()) // CANDLE_S) * CANDLE_S
        self._seed_evaluated_from_db(cs_min=cs_now - CANDLE_S * 2)
        log.info(f"scanner up: tick={TICK_S}s entry_lag={ENTRY_LAG_S}s settle_lag={SETTLE_LAG_S}s "
                 f"strategies={[s.id for s in ACTIVE]} seeded={len(self._evaluated)}")
        while True:
            try:
                await self.tick()
            except Exception as e:
                log.exception(f"tick error: {e}")
            await asyncio.sleep(TICK_S)
