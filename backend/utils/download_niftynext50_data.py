import os
import sys
import csv
import json
import asyncio
import urllib.request
from datetime import datetime, timedelta, date
from pathlib import Path
from dotenv import load_dotenv

# Setup paths
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

load_dotenv(dotenv_path=backend_dir / ".env")

def clean_timestamp(ts_val: float) -> datetime:
    return datetime.fromtimestamp(ts_val)

async def download_stock_data(symbol: str, security_id: str, start_date: date, end_date: date, market_dir: Path) -> bool:
    csv_name = f"{symbol}_3y_5m.csv"
    market_path = market_dir / csv_name
    
    if market_path.exists() and market_path.stat().st_size > 100:
        print(f"  [SKIPPED] {symbol} already exists at {market_path}")
        return True

    print(f"  [STARTING] Downloading 3 years of 5m data for {symbol} ({security_id})...")
    url = "https://api.dhan.co/v2/charts/intraday"
    access_token = os.getenv("ACCESS_TOKEN", "")
    
    if not access_token:
        print("  ERROR: ACCESS_TOKEN not found in .env file.")
        sys.exit(1)
        
    headers = {
        "Content-Type": "application/json",
        "access-token": access_token
    }

    # Split 3 years into 15-day chunks to respect limits
    chunks = []
    curr_start = start_date
    while curr_start < end_date:
        curr_end = min(curr_start + timedelta(days=14), end_date)
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
            "interval": "5",
            "fromDate": c_start.strftime("%Y-%m-%d"),
            "toDate": c_end.strftime("%Y-%m-%d")
        }
        
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                
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
        except Exception as e:
            print(f"    Error fetching chunk {c_start} to {c_end}: {e}")
            
        await asyncio.sleep(0.2) # small rate limit sleep between chunks

    if len(all_candles) == 0:
        print(f"  [WARNING] No records fetched for {symbol}!")
        return False

    # Sort and save
    sorted_times = sorted(all_candles.keys())
    with open(market_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "symbol", "open", "high", "low", "close", "volume"])
        for t_str in sorted_times:
            c = all_candles[t_str]
            writer.writerow([c["timestamp"], c["symbol"], c["open"], c["high"], c["low"], c["close"], c["volume"]])

    print(f"  [SUCCESS] Saved {symbol} to {market_path} ({len(sorted_times)} records, {duplicate_count} dupes)")
    return True

async def main():
    niftynext_csv_path = backend_dir.parent / "market_data" / "niftynext50_security_ids.csv"
    if not niftynext_csv_path.exists():
        print(f"ERROR: Nifty Next 50 ID mapping not found at: {niftynext_csv_path}")
        sys.exit(1)
        
    market_dir = backend_dir.parent / "market_data" / "history" / "next_50"
    market_dir.mkdir(parents=True, exist_ok=True)
    
    end_date = date(2026, 7, 6)
    start_date = end_date - timedelta(days=3 * 365) # 3 years
    
    stocks = []
    with open(niftynext_csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stocks.append((row["symbol"], row["security_id"]))
            
    print(f"Found {len(stocks)} Next 50 stocks to download from {start_date} to {end_date}...\n")
    
    for i, (symbol, sec_id) in enumerate(stocks):
        print(f"[{i+1}/{len(stocks)}] Processing {symbol}...")
        success = await download_stock_data(symbol, sec_id, start_date, end_date, market_dir)
        
        # Rate limit sleep between stocks
        if i < len(stocks) - 1:
            print("  Pausing for 2 seconds before the next stock...")
            await asyncio.sleep(2.0)

    print("\nAll Next 50 downloads finished successfully!")

if __name__ == "__main__":
    asyncio.run(main())
