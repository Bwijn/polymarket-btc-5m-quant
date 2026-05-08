import asyncio
import pytest
from unittest.mock import patch
from conftest import MVP, TOKEN_ID, _insert_open_real, _get_real
from models.trade import TradeStatus


def test_settle_win(executor):
    _insert_open_real(executor)
    with patch("execution.real.get_market_resolution", return_value={TOKEN_ID: True}):
        asyncio.run(executor.check_settlements())
    row = _get_real(executor, 1)
    assert row.status == TradeStatus.settled
    assert row.exit_price == 1.0
    assert row.pnl == pytest.approx(1.0)


def test_settle_loss(executor):
    _insert_open_real(executor)
    with patch("execution.real.get_market_resolution", return_value={TOKEN_ID: False}):
        asyncio.run(executor.check_settlements())
    row = _get_real(executor, 1)
    assert row.status == TradeStatus.settled
    assert row.pnl == pytest.approx(-1.0)


def test_no_settle_when_not_resolved(executor):
    _insert_open_real(executor)
    with patch("execution.real.get_market_resolution", return_value=None):
        asyncio.run(executor.check_settlements())
    row = _get_real(executor, 1)
    assert row.status == TradeStatus.open


def test_no_settle_pending(executor):
    _insert_open_real(executor, status=TradeStatus.pending)
    with patch("execution.real.get_market_resolution", return_value={TOKEN_ID: True}):
        asyncio.run(executor.check_settlements())
    row = _get_real(executor, 1)
    assert row.status == TradeStatus.pending
