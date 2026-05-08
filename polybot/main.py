"""Entry point. Runs paper-trade scanner forever."""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import LOG_FILE, DB_FILE
from scanner import Scanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)


async def _amain():
    scanner = Scanner(DB_FILE)
    await scanner.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass
