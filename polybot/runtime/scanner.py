"""Paper-trade scanner for binary 5m BTC up/down markets.

Each TICK_S seconds:
  1. Trigger eval — for current + previous candle, for each ACTIVE strategy:
     If now ≥ cs + entry_offset_s and (expr, cs) not yet recorded,
     compute features (trades_cache + Binance klines),
     evaluate trigger expression. On hit, INSERT a paper_trade_5m_binary row
     with paper book snapshot only — bt track removed 2026-05-26 (derived
     on-demand via scratch/research/compute_drift.py + PM /trades).
  2. Settle — for each open row where cs+300+SETTLE_LAG_S ≤ now, fetch event
     via PM Gamma /events/slug, parse outcomePrices, compute pnl, UPDATE.

Trigger semantics 1:1 with mining: same expr_eval_v1.evaluate.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from typing import Optional
from sqlmodel import Session, select, SQLModel, create_engine

from polybot.runtime.config import (DB_FILE, SETTLE_LAG_S, PAPER_SIZE_USD,
                            SCHEDULE_REFRESH_S, SETTLE_POLL_S,
                            LIVE_ENABLED, BANKROLL_FRAC, LIVE_MIN_USD, LIVE_SLIPPAGE_CAP)
from polybot.runtime.models import PaperTrade5mBinary, TradeStatus, Direction
from polybot.runtime.live_exec import LiveExecutor, LiveFill
from polybot.runtime.redeem import redeem_loop
from polybot.strategies import ACTIVE, Strategy
from polybot.lib.expr_eval_v1 import evaluate
from polybot.lib.friction import paper_friction_ratio
from polybot.runtime.features import compute_row, needs_klines
from polybot.runtime.pm_api import slug_for, fetch_event, parse_market
from polybot.runtime.pm_ws import BookCache, TradesCache
from polybot.runtime.bn_api import fetch_klines

log = logging.getLogger("polybot.scanner")

CANDLE_S = 300


def _engine(db_path: str):
    eng = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(eng)
    _ensure_dashboard(eng)
    return eng


def _ensure_dashboard(eng) -> None:
    """Materialize ACTIVE → active_strategies table + paper_active_agg view.

    Why: paper_trade_5m_binary lives in this db (polybot.db); ACTIVE lives in
    strategies.py (code); factors registry lives in pm_btc5m.db (cross-db join
    forbidden, CLAUDE.md). So mirror ACTIVE into a small table here on every
    startup → the view auto-filters aggregation to current factors. Killed/old
    factors drop out automatically (not in ACTIVE → not in table → not in view).
    Deploy + restart refreshes the mirror; 0 manual drift.
    Aggregate everything via:  SELECT * FROM paper_active_agg;
    """
    from sqlalchemy import text
    with eng.begin() as c:
        c.execute(text("CREATE TABLE IF NOT EXISTS active_strategies ("
                       "expr TEXT PRIMARY KEY, direction TEXT, entry_offset_s INTEGER, live INTEGER)"))
        c.execute(text("DELETE FROM active_strategies"))
        for s in ACTIVE:
            c.execute(text("INSERT INTO active_strategies (expr,direction,entry_offset_s,live) "
                           "VALUES (:e,:d,:o,:l)"),
                      {"e": s.expr, "d": s.direction, "o": s.entry_offset_s, "l": int(s.live)})
        # net_pnl per-$1 = pnl_ratio_paper_net. n split: n_fired = signal fired & resolved;
        # n_filled = of those, how many had a fillable book (pnl_ratio_paper_net NOT NULL).
        # wr/mean_nev/t all share the n_filled base (no-fill rows can't enter EV) → self-consistent.
        # AVG skips NULL so mean/var are already over filled; t must use n_filled in √n + Bessel too.
        c.execute(text("DROP VIEW IF EXISTS paper_active_agg"))
        c.execute(text("""CREATE VIEW paper_active_agg AS
            SELECT a.expr, a.direction, a.live,
                   COUNT(*) AS n_fired,
                   SUM(p.pnl_ratio_paper_net IS NOT NULL) AS n_filled,
                   ROUND(1.0*SUM(CASE WHEN p.pnl_ratio_paper_net IS NOT NULL
                                       AND ((a.direction='UP'   AND p.up_won=1)
                                         OR (a.direction='DOWN' AND p.up_won=0))
                                      THEN 1 ELSE 0 END)
                         / NULLIF(SUM(p.pnl_ratio_paper_net IS NOT NULL),0), 3) AS wr,
                   ROUND(AVG(p.pnl_ratio_paper_net), 4) AS mean_nev,
                   ROUND(SUM(p.pnl_usd_paper_net), 2) AS sum_usd_net,
                   CASE WHEN SUM(p.pnl_ratio_paper_net IS NOT NULL) > 1 THEN ROUND(
                     AVG(p.pnl_ratio_paper_net) * sqrt(SUM(p.pnl_ratio_paper_net IS NOT NULL)) /
                     sqrt((AVG(p.pnl_ratio_paper_net*p.pnl_ratio_paper_net)
                         - AVG(p.pnl_ratio_paper_net)*AVG(p.pnl_ratio_paper_net))
                         * SUM(p.pnl_ratio_paper_net IS NOT NULL)
                         / (SUM(p.pnl_ratio_paper_net IS NOT NULL)-1.0)), 2)
                   ELSE NULL END AS t,
                   MAX(p.settled_at_s) AS last_settle_s
            FROM paper_trade_5m_binary p
            JOIN active_strategies a ON p.expr = a.expr
            WHERE p.status='settled'
            GROUP BY a.expr, a.direction, a.live"""))


class Scanner:
    def __init__(self, db_path: str = DB_FILE,
                 book_cache: BookCache | None = None,
                 trades_cache: 'TradesCache | None' = None):
        self.engine = _engine(db_path)
        self.db_path = db_path
        self.book_cache = book_cache or BookCache()
        self.trades_cache = trades_cache    # INTRA features need this; None → INTRA = NaN
        # LiveExecutor only when armed — None ⇒ pure paper, no key/network touched.
        self.live = LiveExecutor() if LIVE_ENABLED else None
        # _evaluated: (expr, cs) we've already eval'd (hit or miss).
        # _scheduled: (expr, cs) we've already created an asyncio task for.
        # DB is SSOT for hits; sets are ephemeral — restart re-seeds from DB.
        self._evaluated: set[tuple[str, int]] = set()
        self._scheduled: set[tuple[str, int]] = set()

    def _seed_evaluated_from_db(self, cs_min: int) -> None:
        with Session(self.engine) as s:
            rows = s.exec(
                select(PaperTrade5mBinary.expr, PaperTrade5mBinary.candle_start_s)
                .where(PaperTrade5mBinary.candle_start_s >= cs_min)
            ).all()
        for r in rows:
            self._evaluated.add((r[0], r[1]))

    # ---- trigger ------------------------------------------------------------
    async def eval_one(self, strat: Strategy, cs: int) -> None:
        slug = slug_for(cs)
        ev = await fetch_event(slug)
        if ev is None:
            log.info(f"[{strat.label}] cs={cs} event not found yet (slug={slug})")
            return
        m = parse_market(ev)
        if m is None:
            log.warning(f"[{strat.label}] cs={cs} market unparseable")
            return

        # Only Binance klines need fetching (bn_ exprs); INTRA features come from trades_cache.
        need_klines = needs_klines(strat.expr)
        klines = await fetch_klines(cs - 3600, cs) if need_klines else None
        if need_klines and (klines is None or klines.empty):
            log.warning(f"[{strat.label}] cs={cs} Binance klines fetch empty — skip")
            return

        # engine=self.engine → compute_row 查/写 feature_history (transform 用).
        # 无 transform 的 expr 不触发任何 db op, engine arg 无副作用.
        # trades=trades_cache[cid] → INTRA features (max/min/mean_intra, pmt_*) source.
        # b3295eb migration: INTRA features 改用 trades-based, scanner 必须 pass trades.
        trades = self.trades_cache.get(m['condition_id']) if self.trades_cache else []
        df = compute_row(strat.expr, cs,
                         trades=trades,
                         up_token=m['up_token'], dn_token=m['down_token'],
                         engine=self.engine, klines=klines)
        hit = bool(evaluate(strat.expr, df).iloc[0])
        self._evaluated.add((strat.expr, cs))
        if not hit:
            return

        # bt entry_price NOT computed here — bt track removed (2026-05-26).
        # bt drift now derived on-demand via PM /trades (scratch/research/compute_drift.py).

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
            expr=strat.expr,
            direction=Direction(strat.direction),
            slug=slug,
            condition_id=m['condition_id'],
            up_token=m['up_token'],
            down_token=m['down_token'],
            candle_start_s=cs,
            entry_offset_s=strat.entry_offset_s,
            book_bid_up=book_bid_up,
            book_ask_up=book_ask_up,
            book_bid_dn=book_bid_dn,
            book_ask_dn=book_ask_dn,
            book_ts_ms_up=book_ts_ms_up,
            book_ts_ms_dn=book_ts_ms_dn,
            entry_price_paper=entry_price_paper,
            size_usd_intended=PAPER_SIZE_USD,
            status=TradeStatus.open,
            opened_at_s=int(time.time()),
        )
        with Session(self.engine) as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        log.info(f"[{strat.label}] HIT cs={cs} entry_paper={entry_price_paper} "
                 f"size=${PAPER_SIZE_USD:.2f} #{row.id}")

        # live track: place the real order for promoted strategies. Paper row is
        # already committed above — a live failure never touches the paper track.
        if self.live is not None and strat.live:
            await self._place_live(row.id, strat, m, entry_price_paper)

    # ---- live order placement ----------------------------------------------
    async def _place_live(self, row_id: int, strat: Strategy, m: dict,
                          entry_price_paper: float | None) -> None:
        """Place the real order for a live-strategy HIT (architecture A: same
        row as the paper trade). size = wallet_usdc × BANKROLL_FRAC[label]."""
        if entry_price_paper is None:
            log.warning(f"[{strat.label}] #{row_id} no book snapshot — skip live")
            return
        frac = BANKROLL_FRAC.get(strat.label)
        if frac is None:
            log.error(f"[{strat.label}] #{row_id} live=True but no BANKROLL_FRAC — skip")
            return
        token = m['up_token'] if strat.direction == 'UP' else m['down_token']
        try:
            balance = await asyncio.to_thread(self.live.wallet_usdc)
        except Exception as e:
            log.exception(f"[{strat.label}] #{row_id} balance query failed: {e}")
            return
        size = round(balance * frac, 2)
        if size < LIVE_MIN_USD:
            log.warning(f"[{strat.label}] #{row_id} size ${size:.2f} < ${LIVE_MIN_USD} "
                        f"(wallet ${balance:.2f}) — skip live")
            return
        price_limit = min(0.99, round(entry_price_paper + LIVE_SLIPPAGE_CAP, 2))
        self._set_live_status(row_id, "placing")        # crash-recovery breadcrumb
        fill = await asyncio.to_thread(self.live.buy, token, size, price_limit)
        self._record_live(row_id, fill)
        if fill.success:
            log.info(f"[{strat.label}] #{row_id} LIVE BUY ${fill.usdc_paid:.2f} "
                     f"@ {fill.fill_price:.3f} ({fill.shares:.1f}sh) {fill.order_id}")
        else:
            log.error(f"[{strat.label}] #{row_id} LIVE order failed: {fill.error_msg}")

    def _set_live_status(self, row_id: int, status: str) -> None:
        with Session(self.engine) as s:
            r = s.get(PaperTrade5mBinary, row_id)
            r.order_status_live = status
            s.add(r)
            s.commit()

    def _record_live(self, row_id: int, fill: LiveFill) -> None:
        with Session(self.engine) as s:
            r = s.get(PaperTrade5mBinary, row_id)
            r.order_status_live = fill.status
            r.order_id = fill.order_id
            r.tx_hash_live = fill.tx_hash
            if fill.success:
                r.size_usd_live = fill.usdc_paid
                r.shares_live = fill.shares
                r.entry_price_live = fill.fill_price
                r.fee_usd_live = fill.usdc_paid * paper_friction_ratio(fill.fill_price)
            else:
                r.error_msg = fill.error_msg
            s.add(r)
            s.commit()

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
        # paper pnl ratio (only if we captured book_ask at trigger). Gross of fee;
        # fee_usd_paper / *_net columns below carry the friction-adjusted view.
        # bt pnl + drift_paper_backtest removed 2026-05-26 — derived on-demand via
        # scratch/research/compute_drift.py (PM /trades reproducible truth).
        pnl_ratio_paper = pnl_usd_paper = None
        fee_usd = pnl_usd_paper_net = pnl_ratio_paper_net = None
        if row.entry_price_paper is not None and row.entry_price_paper > 0:
            if my_won:
                pnl_ratio_paper = (1.0 - row.entry_price_paper) / row.entry_price_paper
            else:
                pnl_ratio_paper = -1.0
            pnl_usd_paper = pnl_ratio_paper * row.size_usd_intended
            fee_ratio = paper_friction_ratio(row.entry_price_paper)
            fee_usd = row.size_usd_intended * fee_ratio
            pnl_usd_paper_net = pnl_usd_paper - fee_usd
            pnl_ratio_paper_net = pnl_ratio_paper - fee_ratio

        # live track pnl (only if a real order filled). Winning live pnl is the
        # economic truth at resolution; converting shares→USDC needs redeem.
        pnl_ratio_live = pnl_ratio_live_net = pnl_usd_live = None
        pnl_usd_live_net = pnl_ratio_drift_live = None
        if row.entry_price_live and row.entry_price_live > 0 and row.size_usd_live:
            pnl_ratio_live = ((1.0 - row.entry_price_live) / row.entry_price_live
                              if my_won else -1.0)
            pnl_ratio_live_net = pnl_ratio_live - paper_friction_ratio(row.entry_price_live)
            pnl_usd_live = pnl_ratio_live * row.size_usd_live
            pnl_usd_live_net = pnl_usd_live - (row.fee_usd_live or 0.0)
            if pnl_ratio_paper is not None:
                pnl_ratio_drift_live = pnl_ratio_live - pnl_ratio_paper

        with Session(self.engine) as s:
            db_row = s.get(PaperTrade5mBinary, row.id)
            db_row.status = TradeStatus.settled
            db_row.up_won = m['up_won']
            db_row.pnl_ratio_paper = pnl_ratio_paper
            db_row.pnl_usd_paper = pnl_usd_paper
            db_row.fee_usd_paper = fee_usd
            db_row.pnl_usd_paper_net = pnl_usd_paper_net
            db_row.pnl_ratio_paper_net = pnl_ratio_paper_net
            db_row.pnl_ratio_live = pnl_ratio_live
            db_row.pnl_ratio_live_net = pnl_ratio_live_net
            db_row.pnl_usd_live = pnl_usd_live
            db_row.pnl_usd_live_net = pnl_usd_live_net
            db_row.pnl_ratio_drift_live_paper = pnl_ratio_drift_live
            db_row.settled_at_s = int(time.time())
            s.add(db_row)
            s.commit()

        outcome = "WIN" if my_won else "LOSS"
        paper_str = f"paper={pnl_ratio_paper:+.1%}" if pnl_ratio_paper is not None else "paper=n/a"
        log.info(f"settle #{row.id} [{row.expr[:48]}] {outcome} "
                 f"{paper_str} up_won={m['up_won']}")

    # ---- timer-driven trigger ----------------------------------------------
    async def schedule_one(self, strat: Strategy, cs: int) -> None:
        """Sleep until cs+entry_offset, then evaluate. ms-precision wakeup."""
        target_t = cs + strat.entry_offset_s
        delay = target_t - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        if (strat.expr, cs) in self._evaluated:
            return
        try:
            await self.eval_one(strat, cs)
        except Exception as e:
            log.exception(f"[{strat.label}] cs={cs} eval error: {e}")

    async def schedule_loop(self) -> None:
        """Periodically register schedule_one tasks for current + next candle."""
        log.info(f"schedule loop: strategies={[s.label for s in ACTIVE]}")
        while True:
            try:
                now = int(time.time())
                cs_now  = (now // CANDLE_S) * CANDLE_S
                cs_prev = cs_now - CANDLE_S
                for strat in ACTIVE:
                    for cs in (cs_prev, cs_now, cs_now + CANDLE_S):
                        key = (strat.expr, cs)
                        if key in self._scheduled or key in self._evaluated:
                            continue
                        # too-late: entry window already closed (e.g. after restart)
                        if now > cs + CANDLE_S:
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
        loops = [self.schedule_loop(), self.settle_loop()]
        if LIVE_ENABLED:
            # Component 6: redeem winning live positions → pUSD (frees Kelly
            # bankroll). In-process coroutine; redeem_loop is self-guarded.
            loops.append(redeem_loop(self.db_path))
        await asyncio.gather(*loops)
