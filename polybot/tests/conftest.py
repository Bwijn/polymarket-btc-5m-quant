import pytest
from unittest.mock import patch, MagicMock
from sqlmodel import Session, create_engine, SQLModel, select
from execution.real import RealExecutor
from execution.paper import PaperExecutor
from models.trade import RealTrade, PaperTrade, TradeStatus

MVP = "0xfe787d2da716d60e8acff57fb87eb13cd4d10319"
TOKEN_ID = "tok1"


@pytest.fixture
def executor(tmp_path):
    db = str(tmp_path / "real.db")
    with patch.object(RealExecutor, "_init_client", return_value=MagicMock()), \
         patch("execution.real.REAL_COPY_WALLETS", [MVP]):
        ex = RealExecutor(db)
    # keep wallet patch active for copy_trade calls
    ex._wallet_patch = patch("execution.real.REAL_COPY_WALLETS", [MVP])
    ex._wallet_patch.start()
    yield ex
    ex._wallet_patch.stop()


@pytest.fixture
def paper_executor(tmp_path):
    return PaperExecutor(str(tmp_path / "paper.db"))


def _trade(market_id="mkt1", token_id=TOKEN_ID, price=0.50, wallet=MVP):
    return {"market_id": market_id, "token_id": token_id,
            "title": "Test", "price": price, "wallet": wallet, "size": 5}


def _insert_open_real(ex, market_id="mkt1", wallet=MVP, status=TradeStatus.open, order_id=None):
    row = RealTrade(
        market_id=market_id, token_id=TOKEN_ID, title="Test", side="BUY",
        entry_price=0.50, size=1, source_wallet=wallet, status=status,
        order_id=order_id, opened_at=1000,
    )
    with Session(ex.engine) as s:
        s.add(row)
        s.commit()


def _count_real(ex):
    with Session(ex.engine) as s:
        return len(s.exec(select(RealTrade)).all())


def _get_real(ex, trade_id=None):
    with Session(ex.engine) as s:
        if trade_id:
            return s.get(RealTrade, trade_id)
        return s.exec(select(RealTrade)).first()


def _insert_open_paper(ex, market_id="mkt1", wallet=MVP):
    row = PaperTrade(
        market_id=market_id, token_id=TOKEN_ID, title="Test", side="BUY",
        entry_price=0.50, size=50, source_wallet=wallet,
        status=TradeStatus.open, opened_at=1000,
    )
    with Session(ex.engine) as s:
        s.add(row)
        s.commit()


def _count_paper(ex):
    with Session(ex.engine) as s:
        return len(s.exec(select(PaperTrade)).all())
