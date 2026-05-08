import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import POLL_INTERVAL, LOG_FILE, DB_FILE, COPY_WALLETS, REAL_ENABLED
from ingestion.watcher import WalletWatcher
from execution.paper import PaperExecutor
from execution.real import RealExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("polybot")


async def main():
    log.info("=== Polybot: Copy Trading ===")
    log.info(f"Watching {len(COPY_WALLETS)} wallet(s), {POLL_INTERVAL}s interval, real={'ON' if REAL_ENABLED else 'OFF'}")

    watcher = WalletWatcher()
    paper = PaperExecutor(DB_FILE)
    real = RealExecutor(DB_FILE) if REAL_ENABLED else None

    cycle = 0
    while True:
        cycle += 1
        try:
            new_trades = await watcher.poll()
            for trade in new_trades:
                await paper.copy_trade(trade)
                if real:
                    await real.copy_trade(trade)

            if cycle % 10 == 0:
                await paper.check_settlements()
                if real:
                    await real.check_pending()
                    await real.check_settlements()

            if cycle % 60 == 0:
                p_open = len(paper.get_open_trades())
                p_total = len(paper.get_all_trades())
                status = f"STATUS | paper: open={p_open} total={p_total}"
                if real:
                    r_open = len(real.get_open_trades())
                    r_total = len(real.get_all_trades())
                    status += f" | real: open={r_open} total={r_total}"
                log.info(f"{status} | cycle={cycle}")

        except Exception as e:
            log.error(f"Cycle {cycle} error: {e}", exc_info=True)

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
