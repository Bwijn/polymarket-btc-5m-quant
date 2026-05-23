"""Live order placement on PM CLOB V2.

Context: live execution layer — places real-money FOK market BUY orders for
  promoted strategies (Strategy.live=True) when the scanner trigger fires.
  Only instantiated when config.LIVE_ENABLED is True.
Source: py-clob-client-v2 SDK; creds from .env (PRIVATE_KEY + POLY_API_*).
  Wallet config (signature_type=1 POLY_PROXY, funder) + V2 response shape
  verified by a real $1.20 round-trip — see docs/live_execution_reference.md.
"""
from __future__ import annotations
import logging
import os
from dataclasses import dataclass

from py_clob_client_v2 import (ClobClient, ApiCreds, MarketOrderArgs, OrderType,
                               BalanceAllowanceParams, AssetType)
from py_clob_client_v2.order_builder.constants import BUY

from polybot.runtime.config import CLOB_API, CHAIN_ID, SIG_TYPE, FUNDER

log = logging.getLogger("polybot.live")


@dataclass
class LiveFill:
    """Normalized post-order result. usdc_paid/shares/fill_price set on success."""
    success: bool
    status: str                       # matched / delayed / failed
    order_id: str | None = None
    tx_hash: str | None = None
    usdc_paid: float | None = None    # makingAmount — real cost basis (ex-fee)
    shares: float | None = None       # takingAmount — held until redeem
    fill_price: float | None = None   # usdc_paid / shares
    error_msg: str = ""


class LiveExecutor:
    """One long-lived CLOB V2 client. __init__ does an L2 auth check (fail fast)."""

    def __init__(self) -> None:
        creds = ApiCreds(api_key=os.environ["POLY_API_KEY"],
                         api_secret=os.environ["POLY_API_SECRET"],
                         api_passphrase=os.environ["POLY_API_PASSPHRASE"])
        self.client = ClobClient(CLOB_API, chain_id=CHAIN_ID,
                                 key=os.environ["PRIVATE_KEY"], creds=creds,
                                 signature_type=SIG_TYPE, funder=FUNDER,
                                 retry_on_error=False)
        self.client.assert_level_2_auth()
        log.info(f"live executor ready | signer={self.client.get_address()} "
                 f"funder={FUNDER}")

    def wallet_usdc(self) -> float:
        ba = self.client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL,
                                   signature_type=SIG_TYPE))
        return int(ba["balance"]) / 1e6

    def buy(self, token: str, amount_usd: float, price_limit: float) -> LiveFill:
        """One FOK market BUY. amount_usd = USD notional of shares (PM adds the
        taker fee on top); price_limit = worst-price slippage cap. Sync — the
        caller wraps this in asyncio.to_thread."""
        try:
            # options omitted — create_market_order fetches market-info per-token
            # regardless, so passing tick/neg saves no RTT; letting the SDK
            # resolve them keeps the values always-correct.
            resp = self.client.create_and_post_market_order(
                order_args=MarketOrderArgs(token_id=token, side=BUY,
                                           amount=amount_usd, price=price_limit),
                order_type=OrderType.FOK)
        except Exception as e:
            log.exception(f"buy failed token={token[:12]} amount={amount_usd}")
            return LiveFill(success=False, status="failed", error_msg=str(e))
        return self._parse(resp)

    @staticmethod
    def _as_dict(resp) -> dict:
        if isinstance(resp, dict):
            return resp
        for attr in ("model_dump", "dict"):
            fn = getattr(resp, attr, None)
            if callable(fn):
                try:
                    return fn()
                except Exception:
                    pass
        return getattr(resp, "__dict__", {}) or {}

    @classmethod
    def _parse(cls, resp) -> LiveFill:
        d = cls._as_dict(resp)
        err = d.get("errorMsg") or ""
        status = (d.get("status") or "").lower()
        if not d.get("success"):
            return LiveFill(success=False, status=status or "failed",
                            order_id=d.get("orderID"), error_msg=err)
        making = float(d.get("makingAmount") or 0)   # USDC paid
        taking = float(d.get("takingAmount") or 0)   # shares received
        if taking <= 0:
            return LiveFill(success=False, status="failed",
                            order_id=d.get("orderID"), error_msg=err or "zero fill")
        txs = d.get("transactionsHashes") or []
        return LiveFill(success=True, status=status or "matched",
                        order_id=d.get("orderID"),
                        tx_hash=txs[0] if txs else None,
                        usdc_paid=making, shares=taking,
                        fill_price=making / taking, error_msg=err)
