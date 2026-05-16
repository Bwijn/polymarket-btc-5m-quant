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

from config import (DB_FILE, ENTRY_LAG_S, SETTLE_LAG_S, PAPER_SIZE_USD,
                    SCHEDULE_REFRESH_S, SETTLE_POLL_S)
from models import PaperTrade5mBinary, TradeStatus, Direction
from strategies import ACTIVE, Strategy
from factor.expr_eval_v1 import evaluate
from factor.entry_price import get_price_at
from factor.features import compute_row, needs_klines
from pm_api import slug_for, fetch_event, parse_market, fetch_prices_history
from pm_ws import BookCache
from bn_api import fetch_klines

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
        # _evaluated: (strategy_id, cs) we've already eval'd (hit or miss).
        # _scheduled: (strategy_id, cs) we've already created an asyncio task for.
        # DB is SSOT for hits; sets are ephemeral — restart re-seeds from DB.
        self._evaluated: set[tuple[str, int]] = set()
        self._scheduled: set[tuple[str, int]] = set()

    def _seed_evaluated_from_db(self, cs_min: int) -> None:
        with Session(self.engine) as s:
            rows = s.exec(
                select(PaperTrade5mBinary.hypothesis, PaperTrade5mBinary.candle_start_s)
                .where(PaperTrade5mBinary.candle_start_s >= cs_min)
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

        # 并发 fetch: PM up + dn ticks (always) + Binance klines (only if expr needs).
        # startTs=cs-3600 matches mining backfill — carry-forward fallback for the ~3.9%
        # of candles with no trade in [cs, cs+60].
        need_klines = needs_klines(strat.expr)
        fetches = [
            fetch_prices_history(m['up_token'],   cs - 3600, cs + strat.entry_offset_s, fidelity=1),
            fetch_prices_history(m['down_token'], cs - 3600, cs + strat.entry_offset_s, fidelity=1),
        ]
        if need_klines:
            fetches.append(fetch_klines(cs - 3600, cs))
        results = await asyncio.gather(*fetches)
        ticks_up, ticks_dn = results[0], results[1]
        klines = results[2] if need_klines else None
        if not ticks_up:
            log.info(f"[{strat.id}] cs={cs} no up ticks yet")
            return
        if need_klines and (klines is None or klines.empty):
            log.warning(f"[{strat.id}] cs={cs} Binance klines fetch empty — skip")
            return

        # engine=self.engine → compute_row 查/写 feature_history (transform 用).
        # 无 transform 的 expr (e.g., H5) 不触发任何 db op, engine arg 无副作用.
        df = compute_row(strat.expr, ticks_up, ticks_dn, cs, engine=self.engine, klines=klines)
        hit = bool(evaluate(strat.expr, df).iloc[0])
        self._evaluated.add((strat.id, cs))
        if not hit:
            return

        # Direction-correct entry_price_backtest: 不再 1-p flip, 用 direction 自己的 ticks
        ticks_dir = ticks_up if strat.direction == 'UP' else ticks_dn
        p_dir = get_price_at(ticks_dir, strat.entry_offset_s, cs)
        if p_dir is None:
            log.warning(f"[{strat.id}] cs={cs} HIT but p_{strat.direction.lower()} None — skip")
            return
        entry_price_backtest = p_dir
        # p_up_at_entry: 保留作 audit 字段 (始终是 UP 价, 跨 strategy direction 通用基线)
        p_up = get_price_at(ticks_up, strat.entry_offset_s, cs)

        # paper-side: live orderbook BOTH sides recorded for arb_gap analysis.
        # Entry price = our side's ask (taker BUY crosses spread).
        snap_up = self.book_cache.snapshot(m['up_token'])
        snap_dn = self.book_cache.snapshot(m['down_token'])
        book_bid_up = snap_up['best_bid'] if snap_up else None
        book_ask_up = snap_up['best_ask'] if snap_up else None
        book_bid_dn = snap_dn['best_bid'] if snap_dn else None
        book_ask_dn = snap_dn['best_ask'] if snap_dn else None
        book_ts_ms_up = snap_up['ts_ms'] if snap_up else None
        book_ts_ms_dn = snap_dn['ts_ms'] if snap_dn else None
        entry_price_paper = book_ask_up if strat.direction == 'UP' else book_ask_dn

        row = PaperTrade5mBinary(
            hypothesis=strat.id,
            expr=strat.expr,
            direction=Direction(strat.direction),
            slug=slug,
            market_id=m['market_id'],
            up_token=m['up_token'],
            down_token=m['down_token'],
            candle_start_s=cs,
            entry_offset_s=strat.entry_offset_s,
            p_up_at_entry=p_up,
            entry_price_backtest=entry_price_backtest,
            book_bid_up=book_bid_up,
            book_ask_up=book_ask_up,
            book_bid_dn=book_bid_dn,
            book_ask_dn=book_ask_dn,
            book_ts_ms_up=book_ts_ms_up,
            book_ts_ms_dn=book_ts_ms_dn,
            entry_price_paper=entry_price_paper,
            size_usd=PAPER_SIZE_USD,
            trigger_features=df.iloc[0].to_json(),
            status=TradeStatus.open,
            opened_at_s=int(time.time()),
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
        # backtest pnl ratio
        if my_won:
            pnl_ratio_bt = (1.0 - row.entry_price_backtest) / row.entry_price_backtest
        else:
            pnl_ratio_bt = -1.0
        pnl_usd_bt = pnl_ratio_bt * row.size_usd

        # paper pnl ratio (only if we captured book_ask at trigger)
        pnl_ratio_paper = pnl_usd_paper = pnl_ratio_drift = None
        if row.entry_price_paper is not None and row.entry_price_paper > 0:
            if my_won:
                pnl_ratio_paper = (1.0 - row.entry_price_paper) / row.entry_price_paper
            else:
                pnl_ratio_paper = -1.0
            pnl_usd_paper = pnl_ratio_paper * row.size_usd
            pnl_ratio_drift = pnl_ratio_paper - pnl_ratio_bt

        with Session(self.engine) as s:
            db_row = s.get(PaperTrade5mBinary, row.id)
            db_row.status = TradeStatus.settled
            db_row.up_won = m['up_won']
            db_row.pnl_ratio_backtest = pnl_ratio_bt
            db_row.pnl_ratio_paper = pnl_ratio_paper
            db_row.pnl_ratio_drift = pnl_ratio_drift
            db_row.pnl_usd_backtest = pnl_usd_bt
            db_row.pnl_usd_paper = pnl_usd_paper
            db_row.settled_at_s = int(time.time())
            s.add(db_row)
            s.commit()

        outcome = "WIN" if my_won else "LOSS"
        paper_str = (f"paper={pnl_ratio_paper:+.1%} drift={pnl_ratio_drift:+.1%}"
                     if pnl_ratio_paper is not None else "paper=n/a")
        log.info(f"settle #{row.id} [{row.hypothesis}] {outcome} "
                 f"bt={pnl_ratio_bt:+.1%} {paper_str} up_won={m['up_won']}")

    # ---- timer-driven trigger ----------------------------------------------
    async def schedule_one(self, strat: Strategy, cs: int) -> None:
        """Sleep until cs+entry_offset+ENTRY_LAG_S, then evaluate. ms-precision wakeup."""
        target_t = cs + strat.entry_offset_s + ENTRY_LAG_S
        delay = target_t - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        if (strat.id, cs) in self._evaluated:
            return
        try:
            await self.eval_one(strat, cs)
        except Exception as e:
            log.exception(f"[{strat.id}] cs={cs} eval error: {e}")

    async def schedule_loop(self) -> None:
        """Periodically register schedule_one tasks for current + next candle."""
        log.info(f"schedule loop: entry_lag={ENTRY_LAG_S}s "
                 f"strategies={[s.id for s in ACTIVE]}")
        while True:
            try:
                now = int(time.time())
                cs_now  = (now // CANDLE_S) * CANDLE_S
                cs_prev = cs_now - CANDLE_S
                for strat in ACTIVE:
                    for cs in (cs_prev, cs_now, cs_now + CANDLE_S):
                        key = (strat.id, cs)
                        if key in self._scheduled or key in self._evaluated:
                            continue
                        # too-late: entry window already closed (e.g. after restart)
                        if now > cs + CANDLE_S + ENTRY_LAG_S:
                            self._evaluated.add(key)
                            continue
                        self._scheduled.add(key)
                        asyncio.create_task(self.schedule_one(strat, cs))
                # GC ephemeral sets — keep only last 2 candles
                gc_cutoff = cs_now - CANDLE_S * 2
                self._evaluated = {(h, c) for (h, c) in self._evaluated if c >= gc_cutoff}
                self._scheduled = {(h, c) for (h, c) in self._scheduled if c >= gc_cutoff}
            except Exception as e:
                log.exception(f"schedule_loop error: {e}")
            await asyncio.sleep(SCHEDULE_REFRESH_S)

    # ---- settle loop --------------------------------------------------------
    async def settle_loop(self) -> None:
        log.info(f"settle loop: settle_lag={SETTLE_LAG_S}s poll={SETTLE_POLL_S}s")
        while True:
            try:
                cutoff = int(time.time()) - CANDLE_S - SETTLE_LAG_S
                with Session(self.engine) as s:
                    open_rows = s.exec(
                        select(PaperTrade5mBinary)
                        .where(PaperTrade5mBinary.status == TradeStatus.open)
                        .where(PaperTrade5mBinary.candle_start_s <= cutoff)
                    ).all()
                for row in open_rows:
                    try:
                        await self.settle_one(row)
                    except Exception as e:
                        log.exception(f"settle #{row.id} error: {e}")
            except Exception as e:
                log.exception(f"settle_loop error: {e}")
            await asyncio.sleep(SETTLE_POLL_S)

    async def run_forever(self) -> None:
        # Seed with hits already recorded so we don't re-INSERT after restart
        cs_now = (int(time.time()) // CANDLE_S) * CANDLE_S
        self._seed_evaluated_from_db(cs_min=cs_now - CANDLE_S * 2)
        log.info(f"scanner up seeded={len(self._evaluated)}")
        await asyncio.gather(self.schedule_loop(), self.settle_loop())
