# Live Execution Reference — PM CLOB V2

SSOT for the live order path. Captured from a real $1.20 round-trip probe on
2026-05-20 (`scratch/probe_live_order.py`, raw result kept local in
`scratch/probe_live_order_result.json`). Docs can drift — these are real
on-chain facts.

## Wallet / Auth config

| Field | Value |
|---|---|
| host | `https://clob.polymarket.com` |
| chain_id | 137 (Polygon) |
| signature_type | **1 (POLY_PROXY)** |
| funder | proxy contract addr — the polymarket.com wallet, NOT the signer EOA |
| signer EOA | derived from PRIVATE_KEY; holds $0, signs only |
| credentials | PRIVATE_KEY + POLY_API_KEY/SECRET/PASSPHRASE in `.env` |
| approvals | exchange contracts already at MAX allowance — no approval txns needed |

SDK: `py-clob-client-v2` (V1 cutover 2026-04-28; V1 cannot sign V2 orders).
Init: `ClobClient(host, chain_id=137, key=PK, creds=ApiCreds(...), signature_type=1, funder=PROXY, retry_on_error=True)`.

## Order placement

```python
client.create_and_post_market_order(
    order_args=MarketOrderArgs(token_id=tok, side=BUY, amount=<USD>, price=<worst-price limit>),
    options=PartialCreateOrderOptions(tick_size=str(tick), neg_risk=neg),
    order_type=OrderType.FOK,   # FOK = all-or-nothing; FAK = partial-fill-then-cancel
)
```
- `amount` — BUY: USD notional of shares to buy; SELL: number of shares. **No fee in `amount`** — PM charges the taker fee on top automatically.
- `price` — worst-price limit (slippage cap), not a target.
- `tick_size` / `neg_risk` — from `get_tick_size(tok)` / `get_neg_risk(tok)`. btc-updown-5m: tick `0.01`, neg_risk `False`.
- `get_order_book()` returns a **dict** (not an object). `get_price(tok, 'SELL')` = best ask, `get_price(tok, 'BUY')` = best bid.

## V2 post-order response shape (real capture)

```python
# BUY $1.20 FOK
{'errorMsg': '', 'orderID': '0x…', 'makingAmount': '1.199999',
 'takingAmount': '2.181817', 'status': 'matched',
 'transactionsHashes': ['0x…'], 'success': True}

# SELL 2.18 shares FAK
{'errorMsg': '', 'orderID': '0x…', 'makingAmount': '2.18',
 'takingAmount': '1.1554', 'status': 'matched',
 'transactionsHashes': ['0x…'], 'success': True}
```

| Field | Meaning |
|---|---|
| `success` | bool |
| `status` | **lowercase** `'matched'` — V1 used uppercase `MATCHED`; do NOT reuse V1 enum |
| `orderID` | order hash — persist immediately for crash recovery |
| `makingAmount` | BUY: USDC paid · SELL: shares given |
| `takingAmount` | BUY: shares received · SELL: USDC received |
| `transactionsHashes` | list of on-chain tx hashes |
| `errorMsg` | `''` on success |

fill price — BUY: `makingAmount / takingAmount` · SELL: `takingAmount / makingAmount`.

real.py (V1 copy-trade) also saw a `DELAYED` status (order not immediately
matched → pending, poll via `get_order(order_id)`). Not observed in the V2
FOK probe but handle it.

## Fee model (real-trade confirmed)

- `feeRate = 0.07` (PM "Crypto" category), fixed.
- `fee = shares × feeRate × p × (1-p)` ≡ `fee = notional × feeRate × (1-p)` (identical).
- **Pre-fee**: fee charged ON TOP, not inside `makingAmount`. Real wallet
  outflow for a BUY = `amount × (1 + feeRate×(1-p))`.
- taker only (FOK/FAK market orders are always taker). Maker pays 0.
- buy-and-hold-to-resolution strategy pays the taker fee **once** (entry);
  resolution/redemption is not a trade → no fee.
- Matches `polybot/lib/friction.py` `PM_FEE_RATE_CRYPTO = 0.07` — verified
  against $0.076 of real fees on the round-trip.

## Round-trip probe result

$1.20 FOK BUY @ 0.55 → FAK SELL @ 0.53, position flat (0.0018 share dust).
Net cost $0.1204 = ~$0.044 spread + ~$0.076 fee (2 taker legs).
