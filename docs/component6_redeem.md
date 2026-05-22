# Component 6 — 程序化 redeem (V2) — 已验证 spec

研究 2026-05-21 完成,**每一层都用真实链上交易验证过**。新 session 照此建 Component 6,无未知数。

## 背景

PM V2,proxy 钱包 (signature_type=1)。bot 赢的 conditional tokens (条件代币) 锁在 proxy
wallet,需 redeem (赎回) 换回 pUSD。CLOB SDK 无此功能 —— redeem 是链上操作,经 GSN relayer。

## 三层套娃 (每次 redeem 是三层嵌套)

1. **最内 — redeem**: `CtfCollateralAdapter.redeemPositions(collateralToken, parentCollectionId, conditionId, indexSets)`
2. **中间 — proxy 包装**: `ProxyWalletFactory.proxy([{typeCode, to, value, data}])`
3. **最外 — GSN meta-tx**: `RelayHub.relayCall(from, to, encodedFunction, transactionFee, gasPrice, gasLimit, nonce, signature, approvalData)`

## 合约地址 (Polygon, chain 137)

| 合约 | 地址 |
|---|---|
| CtfCollateralAdapter (redeem 目标, standard market) | `0xAdA100Db00Ca00073811820692005400218FcE1f` |
| Conditional Tokens (CTF) | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| pUSD (collateral) | `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` |
| ProxyWalletFactory | `0xaB45c5A4B0c941a2F231C04C3f49182e1A254052` |
| GSN RelayHub (forwarder) | `0xD216153c06E857cD7f72665E0aF1d7D82172F494` |

账户:signer EOA `0xbd27F221B77d1203C2e3E14824E0056F366cDa6b` / proxy wallet
`0x606970B1b66993A8E36C6CD41c1823317152f7ae`。proxy **已 approve** CtfCollateralAdapter
(`isApprovedForAll=True`)—— 无需 approve tx。

## 内层 calldata

`redeemPositions(address,bytes32,bytes32,uint256[])` selector `0x01b7037c`:
- collateralToken = pUSD
- parentCollectionId = `0x00…00` (32 zero bytes)
- conditionId = 市场 condition id (**唯一 per-redeem 变量**)
- indexSets = `[1, 2]`

proxy 包装:`proxy(ProxyTx[])` selector `0x34ee9791`,ProxyTx = `(typeCode=1, to=CtfCollateralAdapter, value=0, data=内层calldata)`。

## relayCall meta-tx

`relayCall(address,address,bytes,uint256,uint256,uint256,uint256,bytes,bytes)` selector `0x405cec67`:
from=EOA / to=ProxyFactory / encodedFunction=proxy([…]) calldata / transactionFee=0 /
gasPrice=0 / gasLimit≈251471 (inner call gas) / nonce=RelayHub nonce / signature=见下 /
approvalData=`0x` (空)。

## GSN v1 签名配方 (已 ecrecover 验证)

源:RelayHub.sol L1517-1518 + L1303。**已用真实链上 redeem 重建 digest + ecrecover,
精确得出 EOA —— 配方 100% 确认。**

```
hashedMessage = keccak256(
    b"rlx:" + from(20B) + to(20B) + encodedFunction(raw bytes)
    + transactionFee(32B) + gasPrice(32B) + gasLimit(32B) + nonce(32B)
    + relayHub地址(20B) + relay地址(20B) )

digest = keccak256( b"\x19Ethereum Signed Message:\n32" + hashedMessage )

signature = sign(私钥, digest)          # 标准 ECDSA, 65 字节 (r,s,v)
```

- address → 20 字节;uint256 → 32 字节大端;encodedFunction → 原始 bytes(无长度前缀)
- relayHub = `0xD216153c…`;relay = `/relay-payload` 返回的地址
- 这是 EIP-191 personal-sign,**不是 EIP-712**。`eth_account` 的 `encode_defunct`+`sign_message`
  覆盖最后两步,或手动 keccak。

## 完整流程

```
1. GET relayer-v2.polymarket.com/relay-payload?address={EOA}&type=PROXY
   → {relay 地址, nonce}                       (headers: RELAYER_API_KEY + RELAYER_API_KEY_ADDRESS)
2. encodedFunction = proxy([{1, CtfCollateralAdapter, 0, redeemPositions(...)}])
3. 按上面配方算 digest,私钥签名 → signature
4. POST relayer-v2.polymarket.com/submit
   body: {from, to=ProxyFactory, proxyWallet, data=encodedFunction, nonce, signature, type:"PROXY"}
5. 轮询 GET /transaction?id={transactionID} → transactionHash → 等 STATE_CONFIRMED
```

每次 redeem 唯一变量 = `conditionId`(relay/nonce 每次现拉)。

## 参考

- 验证样板 redeem tx:`0xbcb3a0121f4ac500344318a72594682c260e638c957dfa1949253957c6ff6c13`
- 研究脚本 (scratch/, 时间胶囊):`probe_redeem_onchain.py` / `probe_relayer_decode.py` /
  `probe_onchain_tx.py` / `probe_relayhub_source.py` / `verify_gsn_signature.py`
- conditionId 来源:`paper_trade_5m_binary.market_id`(格式是 `0x`+64hex,待 build 时核 == conditionId)

## Component 6 build 待办 (new session)

- 从 live 行 (order_id 非空 / settled / won / 未 redeem) 取 conditionId
- 实现上面流程的 redeem 函数
- 加 `pnl_usd_live_realized` 列,记实际到账,对账 `pnl_usd_live`
- redeem 失败只 revert、不丢钱 → 可安全迭代
- 安全:`PRIVATE_KEY` 只从 `.env` 读、用于签名,**永不打印**
