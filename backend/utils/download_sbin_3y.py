import os
import sys
import json
import csv
import urllib.request
import asyncio
from datetime import datetime, timedelta, date
from pathlib import Path
from dotenv import load_dotenv

# Setup paths
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

load_dotenv(dotenv_path=backend_dir / ".env")

def clean_timestamp(ts_val: float) -> datetime:
    # Convert epoch to datetime (local system time representation)
    return datetime.fromtimestamp(ts_val)

async def download_sbin_history():
    symbol = "SBIN"
    security_id = "3045" # SBIN Token ID on NSE_EQ
    
    end_date = date(2026, 7, 6)
    start_date = end_date - timedelta(days=3 * 365) # 3 years
    
    print(f"--- Downloading 3-Year Historical Data for {symbol} ({start_date} to {end_date}) ---")
    url = "https://api.dhan.co/v2/charts/intraday"
    access_token = os.getenv("ACCESS_TOKEN", "")
    
    if not access_token:
        print("ERROR: ACCESS_TOKEN not found in .env file.")
        sys.exit(1)
        
    headers = {
        "Content-Type": "application/json",
        "access-token": access_token
    }

    # Split 3 years (1095 days) into 30-day chunks to respect Dhan API limits
    chunks = []
    curr_start = start_date
    while curr_start < end_date:
        curr_end = min(curr_start + timedelta(days=29), end_date)
        chunks.append((curr_start, curr_end))
        curr_start = curr_end + timedelta(days=1)

    all_candles = {}
    duplicate_count = 0

    for idx, (c_start, c_end) in enumerate(chunks):
        payload = {
            "securityId": security_id,
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "expiryCode": 0,
            "oi": False,
            "fromDate": c_start.strftime("%Y-%m-%d"),
            "toDate": c_end.strftime("%Y-%m-%d")
        }
        
        print(f"  Fetching chunk {idx+1}/{len(chunks)}: {c_start} to {c_end}...")
        
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                
                # Check response structure
                if "timestamp" in res_data:
                    times = res_data["timestamp"]
                    opens = res_data["open"]
                    highs = res_data["high"]
                    lows = res_data["low"]
                    closes = res_data["close"]
                    volumes = res_data["volume"]
                    
                    for i in range(len(times)):
                        t_val = times[i]
                        dt = clean_timestamp(t_val)
                        dt_str = dt.isoformat()
                        
                        if dt_str in all_candles:
                            duplicate_count += 1
                        else:
                            all_candles[dt_str] = {
                                "timestamp": dt_str,
                                "symbol": symbol,
                                "open": opens[i],
                                "high": highs[i],
                                "low": lows[i],
                                "close": closes[i],
                                "volume": int(volumes[i])
                            }
                else:
                    print(f"    Warning: No candle data in chunk response. Response: {res_data}")
        except Exception as e:
            print(f"    Error fetching chunk {c_start} to {c_end}: {e}")
        
        # Respect rate limits: small sleep between requests
        await asyncio.sleep(0.2)

    # Sort candles by timestamp
    sorted_times = sorted(all_candles.keys())
    
    # Save directory setup
    market_dir = backend_dir.parent / "market_data" / "history"
    market_dir.mkdir(parents=True, exist_ok=True)
    
    csv_name = "SBIN_3y_1m.csv"
    market_path = market_dir / csv_name

    # Write CSV
    with open(market_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "symbol", "open", "high", "low", "close", "volume"])
        for t_str in sorted_times:
            c = all_candles[t_str]
            writer.writerow([c["timestamp"], c["symbol"], c["open"], c["high"], c["low"], c["close"], c["volume"]])

    print(f"\nDownload Complete:")
    print(f"  Saved CSV Path         : {market_path}")
    print(f"  CSV Size (Bytes)       : {market_path.stat().st_size}")
    print(f"  Number of Records      : {len(sorted_times)}")
    print(f"  Duplicate Timestamps   : {duplicate_count}")
    
    if len(sorted_times) > 0:
        print(f"  First Record           : {sorted_times[0]}")
        print(f"  Last Record            : {sorted_times[-1]}")
    else:
        print("  WARNING: No records downloaded!")

if __name__ == "__main__":
    asyncio.run(download_sbin_history())
