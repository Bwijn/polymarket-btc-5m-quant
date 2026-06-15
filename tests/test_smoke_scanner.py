"""Integration smoke test — Scanner runtime wiring, offline, no funds touched.

Context: N1 pre-deploy gate. Proves the real runtime paths work without network or
live wallet: ACTIVE validates, dashboard view builds, compute_row+evaluate run per
expr on a recorded real candle, INTRA features compute from trades, and a HIT row
INSERTs + dedup re-seeds from db (restart-safety).
Source: tests/fixtures/smoke_candle.json (recorded real PM candle).
Expected: all pass on the post-migration schema (expr = sole identity, no hypothesis
column — see migration 001). Any break → exit 1.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text
from sqlmodel import Session

import polybot.runtime.scanner as scanner_mod
from polybot.runtime.scanner import Scanner
from polybot.runtime.pm_ws import BookCache, TradesCache
from polybot.runtime.models import PaperTrade5mBinary, TradeStatus, Direction
from polybot.runtime.features import compute_row, needs_klines
from polybot.lib.expr_eval_v1 import validate, evaluate
from polybot.strategies import ACTIVE

FIX = json.loads((Path(__file__).parent / "fixtures" / "smoke_candle.json").read_text())
# Rebuild fetch_klines's output: cols + ts index. Only bn_/basis_ exprs (e.g. R4) use it.
_KLINE_COLS = ["ts", "open", "high", "low", "close", "volume", "quote_volume", "taker_buy_volume"]
KLINES = pd.DataFrame(FIX["klines"], columns=_KLINE_COLS).set_index("ts")


@pytest.fixture
def scanner(tmp_path, monkeypatch):
    # no-fund-touch invariant: force paper-only BEFORE Scanner.__init__ → self.live=None,
    # so no LiveExecutor (would read PRIVATE_KEY/POLY_API_*) and no redeem loop.
    monkeypatch.setattr(scanner_mod, "LIVE_ENABLED", False)
    sc = Scanner(str(tmp_path / "smoke.db"),
                 book_cache=BookCache(), trades_cache=TradesCache())
    assert sc.live is None, "LIVE_ENABLED=False must yield self.live=None (no wallet)"
    return sc


def _compute(sc, expr):
    kl = KLINES if needs_klines(expr) else None  # mirror scanner: klines only when expr needs
    return compute_row(expr, FIX["cs"],
                       trades=FIX["trades"], up_token=FIX["up_token"],
                       dn_token=FIX["down_token"], engine=sc.engine, klines=kl)


def test_active_exprs_validate():
    # ① every ACTIVE expr parses under the DSL
    assert len(ACTIVE) >= 1
    for s in ACTIVE:
        validate(s.expr)


def test_dashboard_materialized(scanner):
    # ② Scanner init → _engine → _ensure_dashboard built active_strategies + the view
    with scanner.engine.connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM active_strategies")).scalar()
        assert n == len(ACTIVE)
        c.execute(text("SELECT * FROM paper_active_agg")).fetchall()  # view queryable
        # expr-only identity: fresh schema must have no hypothesis column (migration 001)
        cols = [r[1] for r in c.execute(text("PRAGMA table_info(paper_trade_5m_binary)"))]
        assert "hypothesis" not in cols and "expr" in cols


def test_compute_and_evaluate_per_expr(scanner):
    # ③ compute_row + evaluate on a real recorded candle → boolean mask, no exception.
    # cold-start feature_history → transform atoms NaN → predicate False (safe, see
    # features.py self-test); plain-comparison exprs get a definite True/False.
    for s in ACTIVE:
        res = evaluate(s.expr, _compute(scanner, s.expr))
        assert len(res) == 1 and res.dtype == bool


def test_intra_feature_computed_from_trades(scanner):
    # trades-based INTRA compute actually yields a finite value (not all-NaN).
    # std_intra_180_up is a plain base col (no transform) → lands directly in df.
    v = _compute(scanner, "std_intra_180_up<0.04648745432496071").iloc[0]["std_intra_180_up"]
    assert np.isfinite(v), f"INTRA compute from trades produced non-finite {v!r}"


def test_insert_and_seed_roundtrip(scanner):
    # ④ HIT path: INSERT a paper row (no hypothesis — would've crashed pre-migration
    # on NOT NULL), then _seed_evaluated_from_db rebuilds the dedup set keyed on expr
    # (restart-safety). expr is the sole identity now.
    s, cs = ACTIVE[0], FIX["cs"]
    row = PaperTrade5mBinary(
        expr=s.expr, direction=Direction(s.direction),
        slug="btc-smoke", condition_id=FIX["cid"],
        up_token=FIX["up_token"], down_token=FIX["down_token"],
        candle_start_s=cs, entry_offset_s=s.entry_offset_s,
        entry_price_paper=0.5, size_usd_intended=1.7,
        status=TradeStatus.open, opened_at_s=cs,
    )
    with Session(scanner.engine) as sess:
        sess.add(row)
        sess.commit()
    scanner._seed_evaluated_from_db(cs_min=cs - 300)
    assert (s.expr, cs) in scanner._evaluated
