"""Pure-math feature extraction. SSOT (single source of truth) for mining + scanner.

ALL feature math lives in this package's submodules:
    binance    — BN features (klines)
    transforms — rolling z-score / rank stateless functions
    pmtrades   — EP sampling (trades-based); mining/ingestion only, runtime unused

Import DIRECTLY from the submodule — e.g. `from polybot.lib.compute.binance import
compute_bn_features`. No re-export shim: provenance stays explicit, no import-order
coupling. Mining + scanner must NEVER re-implement parallel math — always delegate here.
"""
