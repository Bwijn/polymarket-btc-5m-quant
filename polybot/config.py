import os
from pathlib import Path

_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"

TRADE_SIZE = 50
PRICE_RANGE = (0.05, 0.95)

REAL_ENABLED = os.environ.get("REAL_ENABLED", "false").lower() == "true"
REAL_TRADE_SIZE = 1
FUNDER_ADDRESS = "0x606970B1b66993A8E36C6CD41c1823317152f7ae"

REAL_COPY_WALLETS = [
    # 0xc807 removed 2026-04-19: paper rolling50 EV=-3.79% (lifetime +16.34%), 5-bucket trend mono衰退至-6.6%; real 50笔 EV=-15.55%
    # ferrariChampions + CemeterySun removed 2026-04-11: rolling 50 negative EV
]

POLL_INTERVAL = 30
LOG_FILE = "polybot.log"
DB_FILE = "polybot.db"

COPY_WALLETS = [
    "0xc2e7800b5af46e6093872b177b7a5e7f0563be51",  # beachboy4
    "0x492442eab586f242b53bda933fd5de859c8a3782",  # 0x4924..
    "0x019782cab5d844f02bafb71f512758be78579f3c",  # majorexploiter
    "0xefbc5fec8d7b0acdc8911bdd9a98d6964308f9a2",  # reachingthesky
    "0x59a0744db1f39ff3afccd175f80e6e8dfc239a09",  # Blessed-Sunshine
    "0xf195721ad850377c96cd634457c70cd9e8308057",  # CERTuo
    "0x02227b8f5a9636e895607edd3185ed6ee5598ff7",  # HorizonSplendidView
    "0x6a72f61820b26b1fe4d956e17b6dc2a1ea3033ee",  # (unnamed)
    "0x0c154c190e293b7e5f8d453b5f690c4dc9599a45",  # (unnamed)
    "0x8c80d213c0cbad777d06ee3f58f6ca4bc03102c3",  # SecondWindCapital
    "0x992a5c9c5a0c8f83750b5a18795f2338d9b25e34",  # (unnamed)
    "0x07b8e44b90cc3e91b8d5fe60ea810d2534638e25",  # (unnamed)
    "0x8f037a2e4fd49d11267f4ab874ab7ba745ac64d6",  # Anointed-Connect
    "0xdc876e6873772d38716fda7f2452a78d426d7ab6",  # 432614799197
    "0xb45a797faa52b0fd8adc56d30382022b7b12192c",  # bcda
    "0x2a2c53bd278c04da9962fcf96490e17f3dfb9bc1",  # 0x2a2C..
    "0x37c1874a60d348903594a96703e0507c518fc53a",  # CemeterySun
    "0xc8075693f48668a264b9fa313b47f52712fcc12b",  # (unnamed)
    "0x07921379f7b31ef93da634b688b2fe36897db778",  # ewelmealt
    "0xfd22b8843ae03a33a8a4c5e39ef1e5ff33ebad91",  # (unnamed)
    "0xfe787d2da716d60e8acff57fb87eb13cd4d10319",  # ferrariChampions2026
]
