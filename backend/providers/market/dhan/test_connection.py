import os
import sys
import asyncio
import logging
from pathlib import Path
from dotenv import load_dotenv

# Ensure the backend directory is in python module search path
backend_dir = Path(__file__).resolve().parent.parent.parent.parent
sys.path.append(str(backend_dir))

# Load local environment variables
dotenv_path = backend_dir / ".env"
load_dotenv(dotenv_path=dotenv_path)

# Disable structured logs for Dhan and websockets modules to achieve clean formatted output
logging.getLogger("trade_engine.dhan").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

from providers.market.dhan.market_provider import DhanMarketProvider
from providers.market.dhan.models import MarketPacket

# Configurable Test Target Stock (Default: INFY, security token 1624 on NSE_EQ)
TEST_STOCK_SYMBOL = os.getenv("TEST_STOCK_SYMBOL", "INFY")
TEST_STOCK_TOKEN = os.getenv("TEST_STOCK_TOKEN", "1624")
TEST_STOCK_EXCHANGE = int(os.getenv("TEST_STOCK_EXCHANGE", "1")) # 1 = NSE_EQ

packet_count = 0
subscribed_printed = False

async def handle_packet(packet: MarketPacket):
    global packet_count, subscribed_printed
    
    # Verify this is our subscribed instrument
    if packet.security_id == TEST_STOCK_TOKEN:
        if not subscribed_printed:
            print(f"Subscribed : {TEST_STOCK_SYMBOL}")
            subscribed_printed = True
            
        packet_count += 1
        print(f"LTP : {packet.ltp:.2f}")
        if packet.volume is not None:
            print(f"Volume : {packet.volume}")
        print(f"Timestamp : {packet.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Packet Count : {packet_count}")
        # Flush standard output immediately to show continuous stream
        sys.stdout.flush()

async def main():
    print("Connecting...")
    
    # Initialize and configure market provider
    provider = DhanMarketProvider()
    provider.set_packet_callback(handle_packet)
    
    # Trigger connection
    await provider.start()
    
    # Dhan connects asynchronously. Give it a second to handshake and authorize.
    await asyncio.sleep(2.0)
    
    status = provider.get_status()
    if status["connected"]:
        print("Authentication Successful")
        print("Connected")
    else:
        print("Connection Failed. Check your credentials in .env file.")
        await provider.stop()
        return

    # Subscribe to target stock
    # RequestCode 17 = Quote Data, contains LTP, volume, open, high, low, close
    # Alternatively RequestCode 15 = Ticker Data
    await provider.subscribe(request_code=17, instruments=[(TEST_STOCK_EXCHANGE, TEST_STOCK_TOKEN)])
    
    # Stay active to process ticks
    try:
        while True:
            await asyncio.sleep(1.0)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nStopping feed test...")
    finally:
        await provider.stop()
        print("Test stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
