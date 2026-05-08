import asyncio
import pytest
from unittest.mock import patch
from conftest import MVP, _trade, _insert_open_real, _count_real, _get_real
from models.trade import TradeStatus


# --- Wallet filter ---
def test_rejects_non_mvp_wallet(executor):
    with patch("execution.real.get_last_trade_price") as mock_price:
        asyncio.run(executor.copy_trade(_trade(wallet="0xrandom_nobody")))
    mock_price.assert_not_called()
    assert _count_real(executor) == 0


# --- Dedup ---
def test_dedup_skips_existing_open(executor):
    _insert_open_real(executor)
    with patch("execution.real.get_last_trade_price") as mock_price:
        asyncio.run(executor.copy_trade(_trade()))
    mock_price.assert_not_called()
    assert _count_real(executor) == 1


def test_dedup_skips_existing_pending(executor):
    _insert_open_real(executor, status=TradeStatus.pending)
    with patch("execution.real.get_last_trade_price") as mock_price:
        asyncio.run(executor.copy_trade(_trade()))
    mock_price.assert_not_called()
    assert _count_real(executor) == 1


def test_dedup_allows_different_market(executor):
    _insert_open_real(executor)
    with patch("execution.real.get_last_trade_price", return_value=0.50):
        executor.client.create_market_order.return_value = "signed"
        executor.client.post_order.return_value = {
            "success": True, "status": "matched", "orderID": "ord1",
            "makingAmount": "1000000", "takingAmount": "2000000",
        }
        asyncio.run(executor.copy_trade(_trade(market_id="mkt2", token_id="tok2")))
    assert _count_real(executor) == 2


# --- Price range ---
def test_price_out_of_range(executor):
    with patch("execution.real.get_last_trade_price", return_value=0.97):
        asyncio.run(executor.copy_trade(_trade(price=0.97)))
    assert _count_real(executor) == 0


# --- Drift guard ---
def test_drift_too_large(executor):
    with patch("execution.real.get_last_trade_price", return_value=0.70):
        asyncio.run(executor.copy_trade(_trade(price=0.50)))
    assert _count_real(executor) == 0


# --- Cap price ---
def test_cap_price_tick_aligned_adds_buffer(executor):
    with patch("execution.real.get_last_trade_price", return_value=0.52):
        executor.client.create_market_order.return_value = "signed"
        executor.client.post_order.return_value = {
            "success": True, "status": "matched", "orderID": "ord1",
            "makingAmount": "1000000", "takingAmount": "1923076",
        }
        asyncio.run(executor.copy_trade(_trade(price=0.50)))
    order_args = executor.client.create_market_order.call_args[0][0]
    assert order_args.price == 0.53


def test_cap_price_non_tick_rounds_up(executor):
    with patch("execution.real.get_last_trade_price", return_value=0.527):
        executor.client.create_market_order.return_value = "signed"
        executor.client.post_order.return_value = {
            "success": True, "status": "matched", "orderID": "ord1",
            "makingAmount": "1000000", "takingAmount": "1851851",
        }
        asyncio.run(executor.copy_trade(_trade(price=0.52)))
    order_args = executor.client.create_market_order.call_args[0][0]
    assert order_args.price == 0.54


# --- Order rejected ---
def test_order_rejected(executor):
    with patch("execution.real.get_last_trade_price", return_value=0.50):
        executor.client.create_market_order.return_value = "signed"
        executor.client.post_order.return_value = {"success": False, "errorMsg": "no liquidity"}
        asyncio.run(executor.copy_trade(_trade()))
    assert _count_real(executor) == 0


# --- Order exception ---
def test_order_exception(executor):
    with patch("execution.real.get_last_trade_price", return_value=0.50):
        executor.client.create_market_order.side_effect = Exception("network error")
        asyncio.run(executor.copy_trade(_trade()))
    assert _count_real(executor) == 0


# --- Delayed: stays pending ---
def test_delayed_status_stays_pending(executor):
    with patch("execution.real.get_last_trade_price", return_value=0.50):
        executor.client.create_market_order.return_value = "signed"
        executor.client.post_order.return_value = {
            "success": True, "status": "delayed", "orderID": "ord1",
            "makingAmount": "", "takingAmount": "",
        }
        asyncio.run(executor.copy_trade(_trade()))
    assert _count_real(executor) == 1
    row = _get_real(executor)
    assert row.status == TradeStatus.pending
    assert row.order_id == "ord1"


# --- Delayed dedup ---
def test_delayed_then_dedup(executor):
    with patch("execution.real.get_last_trade_price", return_value=0.50):
        executor.client.create_market_order.return_value = "signed"
        executor.client.post_order.return_value = {
            "success": True, "status": "delayed", "orderID": "ord1",
            "makingAmount": "", "takingAmount": "",
        }
        asyncio.run(executor.copy_trade(_trade()))
        asyncio.run(executor.copy_trade(_trade()))
    assert _count_real(executor) == 1


# --- Happy path ---
def test_successful_order(executor):
    with patch("execution.real.get_last_trade_price", return_value=0.50):
        executor.client.create_market_order.return_value = "signed"
        executor.client.post_order.return_value = {
            "success": True, "status": "matched", "orderID": "ord1",
            "makingAmount": "1000000", "takingAmount": "2000000",
        }
        asyncio.run(executor.copy_trade(_trade()))
    row = _get_real(executor)
    assert row.entry_price == 0.50
    assert row.size == 1
    assert row.order_id == "ord1"
    assert row.status == TradeStatus.open


# --- Fill price extraction ---
def test_fill_price_from_response_not_cur_price(executor):
    with patch("execution.real.get_last_trade_price", return_value=0.50):
        executor.client.create_market_order.return_value = "signed"
        executor.client.post_order.return_value = {
            "success": True, "status": "matched", "orderID": "ord1",
            "makingAmount": "1000000", "takingAmount": "2105263",
        }
        asyncio.run(executor.copy_trade(_trade()))
    row = _get_real(executor)
    assert row.entry_price == pytest.approx(0.475, rel=1e-3)


def test_fill_price_fallback_no_amounts(executor):
    with patch("execution.real.get_last_trade_price", return_value=0.50):
        executor.client.create_market_order.return_value = "signed"
        executor.client.post_order.return_value = {
            "success": True, "status": "matched", "orderID": "ord1",
        }
        asyncio.run(executor.copy_trade(_trade()))
    row = _get_real(executor)
    assert row.entry_price == 0.50
