import asyncio
import pytest
from unittest.mock import patch
from sqlmodel import Session, select
from conftest import MVP, TOKEN_ID, _trade, _insert_open_paper, _count_paper
from models.trade import PaperTrade, TradeStatus


def test_paper_copies_any_wallet(paper_executor):
    asyncio.run(paper_executor.copy_trade(_trade(wallet="0xrandom_nobody")))
    assert _count_paper(paper_executor) == 1


def test_paper_happy_path(paper_executor):
    asyncio.run(paper_executor.copy_trade(_trade(price=0.50)))
    with Session(paper_executor.engine) as s:
        row = s.exec(select(PaperTrade)).first()
    assert row.entry_price == 0.50
    assert row.size == 50
    assert row.status == TradeStatus.open
    assert row.source_wallet == MVP


def test_paper_dedup_same_market_and_wallet(paper_executor):
    _insert_open_paper(paper_executor)
    asyncio.run(paper_executor.copy_trade(_trade()))
    assert _count_paper(paper_executor) == 1


def test_paper_allows_different_wallet_same_market(paper_executor):
    _insert_open_paper(paper_executor, wallet=MVP)
    asyncio.run(paper_executor.copy_trade(_trade(wallet="0xother_wallet_addr")))
    assert _count_paper(paper_executor) == 2


def test_paper_allows_different_market_same_wallet(paper_executor):
    _insert_open_paper(paper_executor, market_id="mkt1")
    asyncio.run(paper_executor.copy_trade(_trade(market_id="mkt2", token_id="tok2")))
    assert _count_paper(paper_executor) == 2


def test_paper_price_out_of_range(paper_executor):
    asyncio.run(paper_executor.copy_trade(_trade(price=0.97)))
    assert _count_paper(paper_executor) == 0


def test_paper_price_at_lower_bound(paper_executor):
    asyncio.run(paper_executor.copy_trade(_trade(price=0.05)))
    assert _count_paper(paper_executor) == 1


def test_paper_settle_win(paper_executor):
    _insert_open_paper(paper_executor)
    with patch("execution.paper.get_market_resolution", return_value={TOKEN_ID: True}):
        asyncio.run(paper_executor.check_settlements())
    with Session(paper_executor.engine) as s:
        row = s.get(PaperTrade, 1)
    assert row.status == TradeStatus.settled
    assert row.exit_price == 1.0
    assert row.pnl == pytest.approx(50.0)


def test_paper_settle_loss(paper_executor):
    _insert_open_paper(paper_executor)
    with patch("execution.paper.get_market_resolution", return_value={TOKEN_ID: False}):
        asyncio.run(paper_executor.check_settlements())
    with Session(paper_executor.engine) as s:
        row = s.get(PaperTrade, 1)
    assert row.status == TradeStatus.settled
    assert row.pnl == pytest.approx(-50.0)


def test_paper_no_settle_when_not_resolved(paper_executor):
    _insert_open_paper(paper_executor)
    with patch("execution.paper.get_market_resolution", return_value=None):
        asyncio.run(paper_executor.check_settlements())
    with Session(paper_executor.engine) as s:
        row = s.get(PaperTrade, 1)
    assert row.status == TradeStatus.open
