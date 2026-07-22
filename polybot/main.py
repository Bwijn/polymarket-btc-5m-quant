"""Entry point. Concurrently runs:
  - WsBookManager   keeps BookCache hot via PM CLOB ws market channel
  - Scanner         tick loop: trigger eval + settle, reads BookCache for paper entry
"""
import asyncio
import logging
import sys
from pathlib import Path

# Add parent dir to sys.path so `polybot.*` resolves as package.
sys.path.insert(0, str(Path(__file__).parent.parent))

from polybot.runtime.config import LOG_FILE, DB_FILE
from polybot.runtime.pm_ws import BookCache, WsBookManager
from polybot.runtime.scanner import Scanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)


async def _amain():
    cache = BookCache()
    ws_mgr = WsBookManager(cache)
    scanner = Scanner(DB_FILE, book_cache=cache)
    try:
        await asyncio.gather(
            ws_mgr.run_forever(),
            scanner.run_forever(),
        )
    finally:
        await ws_mgr.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass
