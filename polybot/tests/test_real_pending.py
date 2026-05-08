import asyncio
import pytest
from conftest import _insert_open_real, _count_real, _get_real
from models.trade import TradeStatus


def test_pending_expired_deleted(executor):
    _insert_open_real(executor, status=TradeStatus.pending, order_id="ord_delayed1")
    executor.client.get_order.return_value = None
    asyncio.run(executor.check_pending())
    assert _count_real(executor) == 0


def test_pending_filled_becomes_open(executor):
    _insert_open_real(executor, status=TradeStatus.pending, order_id="ord_delayed2")
    executor.client.get_order.return_value = {
        "status": "MATCHED",
        "original_size": "1000000",
        "size_matched": "1851851",
        "price": "0.54",
    }
    asyncio.run(executor.check_pending())
    assert _count_real(executor) == 1
    row = _get_real(executor)
    assert row.status == TradeStatus.open
    assert row.entry_price == pytest.approx(0.54, rel=1e-2)


def test_pending_canceled_deleted(executor):
    _insert_open_real(executor, status=TradeStatus.pending, order_id="ord_delayed3")
    executor.client.get_order.return_value = {
        "status": "CANCELED",
    }
    asyncio.run(executor.check_pending())
    assert _count_real(executor) == 0


def test_pending_still_waiting(executor):
    _insert_open_real(executor, status=TradeStatus.pending, order_id="ord_delayed4")
    executor.client.get_order.return_value = {
        "status": "LIVE",
        "original_size": "1000000", "size_matched": "0",
    }
    asyncio.run(executor.check_pending())
    assert _count_real(executor) == 1
    row = _get_real(executor)
    assert row.status == TradeStatus.pending


def test_pending_no_order_id_skipped(executor):
    _insert_open_real(executor, status=TradeStatus.pending, order_id=None)
    asyncio.run(executor.check_pending())
    assert _count_real(executor) == 1
    executor.client.get_order.assert_not_called()


def test_pending_api_error_survives(executor):
    _insert_open_real(executor, status=TradeStatus.pending, order_id="ord_delayed5")
    executor.client.get_order.side_effect = Exception("timeout")
    asyncio.run(executor.check_pending())
    assert _count_real(executor) == 1
