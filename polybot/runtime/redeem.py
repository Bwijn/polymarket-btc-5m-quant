"""Component 6 — automatic redeem of winning live positions.

Context: winning conditional tokens pile up locked in the proxy wallet → bot
  pUSD bankroll shrinks → Kelly position sizing decays. This module sweeps
  settled+won live positions and redeems them to pUSD via the official PM
  relayer SDK, on a 30-min in-process loop (a coroutine in the scanner gather).
Source: polybot.db (winners) + Polygon RPC (on-chain held check) + relayer-v2
  (gasless submit). redeemPositions routes via CtfCollateralAdapter — the V2
  conditions are USDC.e-collateralised; the adapter wraps the payout to pUSD.
Idempotent: a redeemed row gets its redeem_tx set; the on-chain held-check is
  the safety net against any double-submit.
Schema: the redeem_tx column is added once by scratch/migrate_add_redeem_tx.py —
  run it before the first deploy of this module.
CLI: uv run --project polybot python -m polybot.runtime.redeem [--dry] [db_path]
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

from sqlalchemy import text
from sqlalchemy.pool import NullPool
from sqlmodel import create_engine
from web3 import Web3
from eth_abi import encode
from eth_utils import keccak, to_checksum_address
from py_builder_relayer_client.client import RelayClient
from py_builder_relayer_client.models import Transaction, RelayerTxType
from py_builder_relayer_client.http_helpers.helpers import post as _sdk_post

from polybot.runtime.config import CHAIN_ID, FUNDER, DB_FILE

log = logging.getLogger("polybot.redeem")

# ---- Polygon (chain 137) contracts — source: docs.polymarket.com/resources/
#      contracts; each verified on-chain 2026-05-21. Stable, never change. ----
ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"   # CtfCollateralAdapter
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"      # pUSD collateral token
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"       # Conditional Tokens
RELAYER_URL = "https://relayer-v2.polymarket.com"
# Public Polygon RPC nodes — rotated on failure (public nodes are flaky).
RPCS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.llamarpc.com",
    "https://rpc.ankr.com/polygon",
    "https://1rpc.io/matic",
    "https://polygon-rpc.com",
    "https://polygon.drpc.org",
]
SWEEP_INTERVAL_S = 1800   # 30 min

_CTF_ABI = [{"name": "balanceOf", "type": "function", "stateMutability": "view",
             "inputs": [{"type": "address"}, {"type": "uint256"}],
             "outputs": [{"type": "uint256"}]}]
_SELECTOR = keccak(text="redeemPositions(address,bytes32,bytes32,uint256[])")[:4]

# Direction-aware winner filter: the bot buys ONE side per direction (scanner
# `_place_live`), so a winner is UP-bet+UP-won OR DOWN-bet+DOWN-won (up_won==0).
# The token we hold = up_token if direction=UP else down_token.
_WINNERS_SQL = text(
    "SELECT id, market_id, up_token, down_token, direction "
    "FROM paper_trade_5m_binary "
    "WHERE order_id IS NOT NULL AND status = 'settled' AND redeem_tx IS NULL "
    "AND ((direction = 'UP' AND up_won = 1) OR (direction = 'DOWN' AND up_won = 0))"
)


class _RelayerKeyClient(RelayClient):
    """RelayClient authed via Relayer API Key headers.

    The relayer accepts two equal auth methods (builder-HMAC / relayer-key); the
    SDK only wires builder-HMAC. We override the one POST path to send the
    RELAYER_API_KEY headers — the auth the user provisioned. All transaction
    building / GSN signing stays the SDK's, untouched.
    """

    def __init__(self, *a, relayer_api_key, relayer_api_key_address, **kw):
        super().__init__(*a, **kw)
        self._rk = relayer_api_key
        self._rka = relayer_api_key_address

    def assert_builder_creds_needed(self):  # relayer-key auth → no builder creds
        pass

    def _post_request(self, method, request_path, body=None):
        headers = {"RELAYER_API_KEY": self._rk, "RELAYER_API_KEY_ADDRESS": self._rka}
        return _sdk_post(f"{self.relayer_url}{request_path}", headers=headers, data=body)


def _make_engine(db_path: str):
    """Own engine — NullPool + check_same_thread=False so the sweep is safe to
    run from asyncio.to_thread worker threads. timeout=30 waits out brief SQLite
    write-locks from the trading loop instead of erroring."""
    return create_engine(f"sqlite:///{db_path}", poolclass=NullPool,
                         connect_args={"check_same_thread": False, "timeout": 30})


def _with_w3(fn, tries: int = 18):
    """fn(w3)->result; rotate public RPCs + retry (public nodes are flaky)."""
    last: Exception | None = None
    for i in range(tries):
        rpc = RPCS[i % len(RPCS)]
        try:
            return fn(Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15})))
        except Exception as e:   # noqa: BLE001 — any RPC error → rotate + retry
            last = e
            time.sleep(1.0)
    raise last  # type: ignore[misc]


def _held(token_id: int) -> int:
    """Proxy wallet's ERC-1155 balance of token_id (raw, 6-decimals)."""
    return _with_w3(lambda w3: w3.eth.contract(
        address=to_checksum_address(CTF), abi=_CTF_ABI
    ).functions.balanceOf(to_checksum_address(FUNDER), token_id).call())


def _inner_calldata(condition_id: str) -> str:
    """redeemPositions(pUSD, 0x00, conditionId, [1,2]) calldata. indexSets
    [1,2] redeems both outcomes — only the winning side actually pays out."""
    args = encode(["address", "bytes32", "bytes32", "uint256[]"],
                  [to_checksum_address(PUSD), b"\x00" * 32,
                   bytes.fromhex(condition_id[2:]), [1, 2]])
    return "0x" + (_SELECTOR + args).hex()


def _client() -> _RelayerKeyClient:
    return _RelayerKeyClient(
        relayer_url=RELAYER_URL, chain_id=CHAIN_ID,
        private_key=os.environ["PRIVATE_KEY"], builder_config=None,
        relay_tx_type=RelayerTxType.PROXY,
        relayer_api_key=os.environ["RELAYER_API_KEY"],
        relayer_api_key_address=os.environ["RELAYER_API_KEY_ADDRESS"],
    )


def _mark(engine, row_id: int, redeem_tx: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE paper_trade_5m_binary SET redeem_tx = :tx "
                          "WHERE id = :id"), {"tx": redeem_tx, "id": row_id})


def redeem_sweep(engine, dry_run: bool = False) -> None:
    """One sweep: redeem every unredeemed winning live position.

    Sync — the loop wraps this in asyncio.to_thread. Idempotent + per-position
    guarded: one bad position never blocks the rest, and a failed position is
    simply retried next sweep (its redeem_tx stays NULL).
    """
    with engine.connect() as conn:
        winners = conn.execute(_WINNERS_SQL).fetchall()
    if not winners:
        log.debug("redeem sweep: nothing to redeem")
        return
    log.info(f"redeem sweep: {len(winners)} unredeemed winning position(s)"
             f"{' [DRY RUN]' if dry_run else ''}")

    client: _RelayerKeyClient | None = None
    done = failed = already = 0
    for w in winners:
        token = int(w.up_token if w.direction == "UP" else w.down_token)
        try:
            held = _held(token)
        except Exception as e:   # noqa: BLE001
            log.warning(f"redeem #{w.id}: held-check failed "
                        f"({type(e).__name__}) — retry next sweep")
            failed += 1
            continue
        if held == 0:
            # already redeemed outside this loop (e.g. the pre-C6 manual sweep)
            if not dry_run:
                _mark(engine, w.id, "external")
            log.info(f"redeem #{w.id}: on-chain held=0 — already redeemed, "
                     f"marked 'external'")
            already += 1
            continue
        if dry_run:
            log.info(f"redeem #{w.id}: [DRY] would redeem {held / 1e6:.4f} pUSD "
                     f"({w.direction}, cond {w.market_id[:14]}…)")
            continue
        try:
            if client is None:
                client = _client()
            tx = Transaction(to=to_checksum_address(ADAPTER),
                             data=_inner_calldata(w.market_id), value="0")
            result = client.execute([tx], f"redeem-row-{w.id}").wait()
        except Exception:        # noqa: BLE001
            log.exception(f"redeem #{w.id}: submit failed — retry next sweep")
            failed += 1
            continue
        state = result.get("state") if result else None
        if state in ("STATE_MINED", "STATE_CONFIRMED"):
            txh = result.get("transactionHash")
            _mark(engine, w.id, txh)
            log.info(f"redeem #{w.id}: OK {state} {held / 1e6:.4f} pUSD  {txh}")
            done += 1
        else:
            log.error(f"redeem #{w.id}: non-terminal state={state} "
                      f"— retry next sweep")
            failed += 1
    if not dry_run:
        log.info(f"redeem sweep done: {done} redeemed, {failed} failed, "
                 f"{already} already-done")


async def redeem_loop(db_path: str = DB_FILE) -> None:
    """In-process redeem loop — a coroutine in the scanner gather.

    Sweeps on startup then every 30 min. Bulletproof: any failure is logged and
    NEVER propagates — an uncaught exception here would bubble through
    asyncio.gather and crash the whole bot.
    """
    engine = _make_engine(db_path)
    log.info(f"redeem loop up — sweep every {SWEEP_INTERVAL_S}s")
    while True:
        try:
            await asyncio.to_thread(redeem_sweep, engine)
        except Exception:        # noqa: BLE001 — guardrail, must catch everything
            log.exception("redeem sweep crashed")
        await asyncio.sleep(SWEEP_INTERVAL_S)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
    _pos = [a for a in sys.argv[1:] if not a.startswith("-")]
    _db = _pos[0] if _pos else DB_FILE
    redeem_sweep(_make_engine(_db), dry_run="--dry" in sys.argv)
