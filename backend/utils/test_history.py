import asyncio
import sys
from pathlib import Path

backend_dir = Path("c:/Projects/trade-engine/backend")
sys.path.append(str(backend_dir))

from utils.run_full_backtest import run_simulation

async def main():
    await run_simulation()

if __name__ == "__main__":
    asyncio.run(main())
