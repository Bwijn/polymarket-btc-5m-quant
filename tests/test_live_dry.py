"""Contract / dry-run test for the LIVE order path — asserts order construction
without ever submitting, touching a wallet, or hitting the network.

Context: N6 safety gate before arming R4 (Strategy.live=True). `_place_live` →
LiveExecutor.buy is the highest-risk, least-exercised code (paper track never
calls it) — a wrong token/size/price here loses real money in a way the 5% size
cap can't catch. Two layers mocked:
  A. _place_live order math (mock LiveExecutor records buy() args)
  B. LiveExecutor.buy SDK-arg construction + _parse (mock clob client)
Expected: all pass; no order ever submitted. no-fund-touch invariant enforced.
"""
import asyncio

import pytest

import polybot.runtime.scanner as scanner_mod
from polybot.runtime.scanner import Scanner
from polybot.runtime.pm_ws import BookCache, TradesCache
from polybot.runtime.models import PaperTrade5mBinary, TradeStatus, Direction
from polybot.runtime.live_exec import LiveExecutor, LiveFill
from polybot.runtime.config import BANKROLL_FRAC, LIVE_MIN_USD, LIVE_SLIPPAGE_CAP
from polybot.strategies import R4

from py_clob_client_v2 import OrderType
from py_clob_client_v2.order_builder.constants import BUY

UP_TOK, DN_TOK = "up_token_xxx", "down_token_yyy"
M = {"up_token": UP_TOK, "down_token": DN_TOK}


class FakeLive:
    """Stand-in for LiveExecutor — records buy() args, never submits."""
    def __init__(self, balance, fill):
        self._balance, self._fill, self.calls = balance, fill, []

    def wallet_usdc(self):
        return self._balance

    def buy(self, token, amount_usd, price_limit):
        self.calls.append((token, amount_usd, price_limit))
        return self._fill


def _scanner_with_fake_live(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(scanner_mod, "LIVE_ENABLED", False)   # no real LiveExecutor
    sc = Scanner(str(tmp_path / "live.db"), book_cache=BookCache(), trades_cache=TradesCache())
    assert sc.live is None
    sc.live = fake                                            # inject mock
    return sc


def _seed_row(sc, cs=1779805500):
    row = PaperTrade5mBinary(
        expr=R4.expr, direction=Direction(R4.direction), slug="x", condition_id="0xc",
        up_token=UP_TOK, down_token=DN_TOK, candle_start_s=cs, entry_offset_s=R4.entry_offset_s,
        size_usd_intended=1.7, status=TradeStatus.open, opened_at_s=cs)
    from sqlmodel import Session
    with Session(sc.engine) as s:
        s.add(row)
        s.commit()
        s.refresh(row)
    return row.id


# ── A. _place_live order construction ────────────────────────────────────────

def test_place_live_buys_correct_side_size_price(tmp_path, monkeypatch):
    fill = LiveFill(success=True, status="matched", order_id="0xo", tx_hash="0xt",
                    usdc_paid=5.0, shares=9.5, fill_price=5.0 / 9.5)
    fake = FakeLive(balance=100.0, fill=fill)
    sc = _scanner_with_fake_live(tmp_path, monkeypatch, fake)
    rid = _seed_row(sc)
    ep = 0.527
    asyncio.run(sc._place_live(rid, R4, M, ep))

    assert len(fake.calls) == 1, "exactly one order placed"
    token, size, price_limit = fake.calls[0]
    assert token == DN_TOK, "R4 is DOWN → must buy the DOWN token"
    assert size == round(100.0 * BANKROLL_FRAC["R4"], 2)        # 5% of bankroll
    assert price_limit == min(0.99, round(ep + LIVE_SLIPPAGE_CAP, 2))
    # _record_live persisted the fill onto the row
    from sqlmodel import Session
    with Session(sc.engine) as s:
        r = s.get(PaperTrade5mBinary, rid)
    assert r.order_status_live == "matched" and r.size_usd_live == 5.0
    assert r.entry_price_live == fill.fill_price


def test_place_live_skips_below_min_notional(tmp_path, monkeypatch):
    # balance so small that size < LIVE_MIN_USD → must NOT place an order
    fake = FakeLive(balance=LIVE_MIN_USD / BANKROLL_FRAC["R4"] / 2,  # → size = half the floor
                    fill=LiveFill(success=True, status="matched"))
    sc = _scanner_with_fake_live(tmp_path, monkeypatch, fake)
    rid = _seed_row(sc)
    asyncio.run(sc._place_live(rid, R4, M, 0.5))
    assert fake.calls == [], "size below LIVE_MIN_USD must skip live order"


def test_place_live_caps_price_limit_at_099(tmp_path, monkeypatch):
    fake = FakeLive(balance=100.0, fill=LiveFill(success=True, status="matched",
                                                 usdc_paid=5.0, shares=5.1, fill_price=5.0 / 5.1))
    sc = _scanner_with_fake_live(tmp_path, monkeypatch, fake)
    rid = _seed_row(sc)
    asyncio.run(sc._place_live(rid, R4, M, 0.98))               # 0.98+0.03=1.01 → cap 0.99
    assert fake.calls[0][2] == 0.99


# ── B. LiveExecutor.buy SDK args + _parse ────────────────────────────────────

class FakeClient:
    def __init__(self, resp):
        self.resp, self.recorded = resp, None

    def create_and_post_market_order(self, order_args, order_type):
        self.recorded = (order_args, order_type)
        return self.resp


def _executor_with_fake_client(resp):
    le = LiveExecutor.__new__(LiveExecutor)   # bypass __init__ (no creds / network)
    le.client = FakeClient(resp)
    return le


def test_buy_builds_fok_market_order_args():
    resp = {"success": True, "status": "matched", "orderID": "0x1",
            "makingAmount": "5.0", "takingAmount": "9.5", "transactionsHashes": ["0xtx"]}
    le = _executor_with_fake_client(resp)
    fill = le.buy("tok123", 5.0, 0.56)

    args, otype = le.client.recorded
    assert args.token_id == "tok123" and args.side == BUY
    assert args.amount == 5.0 and args.price == 0.56
    assert otype == OrderType.FOK
    # _parse: cost basis from making/taking
    assert fill.success and fill.usdc_paid == 5.0 and fill.shares == 9.5
    assert fill.fill_price == pytest.approx(5.0 / 9.5)
    assert fill.tx_hash == "0xtx"


def test_buy_zero_fill_is_failure():
    # success flag true but zero shares → must be treated as FAILURE (no phantom fill)
    le = _executor_with_fake_client({"success": True, "status": "matched",
                                     "makingAmount": "0", "takingAmount": "0"})
    fill = le.buy("tok", 5.0, 0.56)
    assert not fill.success


def test_buy_rejected_order_is_failure():
    le = _executor_with_fake_client({"success": False, "status": "unmatched",
                                     "errorMsg": "not enough balance"})
    fill = le.buy("tok", 5.0, 0.56)
    assert not fill.success and "balance" in fill.error_msg
