import logging
from db.store import Store

log = logging.getLogger("polybot")

# From Joshbazz/polymarket_copy_trader proxy_wallets.py
# These are publicly listed top Polymarket leaderboard wallets
SEED_WALLETS = [
    ("0xe43c7b9adddeaf01ba16bc3c5b27f034407801ad", "larpas"),
    ("0x40324412290b9d311f03b707467c61d88cf141a2", "50-Pences"),
    ("0xe64ffa7621459172ad81960762d5760e65d444a5", "HaterzLoserz"),
    ("0xd31a2ea0b5f9a10c2eb78dcc36df016497d5386e", "DarthVooncer"),
    ("0x9f47f1fcb1701bf9eaf31236ad39875e5d60af93", "TheGuru"),
    ("0xe5c8026239919339b988fdb150a7ef4ea196d3e7", "bama124"),
    ("0x5bffcf561bcae83af680ad600cb99f1184d6ffbe", "YatSen"),
    ("0xed107a85a4585a381e48c7f7ca4144909e7dd2e5", "bobe2"),
    ("0x2a1441dc244432d268a103fd7d4a5559813f0762", "weedle"),
    ("0x1f2dd6d473f3e824cd2f8a89d9c69fb96f6ad0cf", "Fredi9999"),
    ("0xfffe4013adfe325c6e02d36dc66e091f5476f52c", "therealbatman"),
    ("0x6139c42e48cf190e67a0a85d492413b499336b7a", "RememberAmalek"),
    ("0xd6c3e3262f679b18c074821bfbd80efa76c84597", "DONNYDARKO"),
    ("0xf8ba34bf0e95d952d05b578bfbc0833f9242a286", "kingfisher"),
    ("0xed72c1aec62993cbc3458b3ec384a52b3defe319", "Champ"),
    ("0xda03aa12853df66d41eaa3ac2b2ce1222503a62c", "Neonview"),
    ("0x79f293c48f651baa31c8086a228102f57b127620", "notgonnatrickme"),
    ("0x128a251f2300694ae6ea9d77e076ea8ea4e097c2", "monchi"),
    ("0x1fc540a4644f665458aefc4f72240d37d37938d9", "Rrdd11"),
    ("0xc9b6227a295985591fe576ff2e054267a78a9b6a", "mango-lassi"),
]


def seed(store: Store):
    for addr, name in SEED_WALLETS:
        store.upsert_wallet(
            addr=addr,
            roi=0.0,
            winrate=0.0,
            sharpe=0.0,
            avg_edge=0.0,
            trade_count=0,
            category=name,
        )
    log.info(f"Seeded {len(SEED_WALLETS)} wallets from leaderboard")
