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

def download_dhan_scrip_master(output_path: Path):
    url = "https://images.dhan.co/api-data/api-scrip-master.csv"
    print(f"Downloading Dhan Scrip Master from: {url}...")
    try:
        urllib.request.urlretrieve(url, output_path)
        print("Successfully downloaded Dhan Scrip Master.")
    except Exception as e:
        print(f"Error downloading Dhan Scrip Master: {e}")
        sys.exit(1)

async def download_chunk(sem, url, payload, headers, symbol, all_candles, lock):
    async with sem:
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST"
                )
                def make_request():
                    with urllib.request.urlopen(req, timeout=15) as r:
                        return json.loads(r.read().decode("utf-8"))
                
                res_data = await asyncio.to_thread(make_request)
                
                if "timestamp" in res_data:
                    times = res_data["timestamp"]
                    opens = res_data["open"]
                    highs = res_data["high"]
                    lows = res_data["low"]
                    closes = res_data["close"]
                    volumes = res_data["volume"]
                    
                    async with lock:
                        for i in range(len(times)):
                            t_val = times[i]
                            dt = clean_timestamp(t_val)
                            dt_str = dt.isoformat()
                            
                            all_candles[dt_str] = {
                                "timestamp": dt_str,
                                "symbol": symbol,
                                "open": opens[i],
                                "high": highs[i],
                                "low": lows[i],
                                "close": closes[i],
                                "volume": int(volumes[i])
                            }
                break # Success, break retry loop
            except Exception as e:
                if attempt == 2:
                    print(f"    Error fetching chunk {payload['fromDate']} to {payload['toDate']}: {e}")
                else:
                    await asyncio.sleep(0.5)

async def download_stock_data(symbol: str, security_id: str, start_date: date, end_date: date, market_dir: Path, sem: asyncio.Semaphore) -> bool:
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
    lock = asyncio.Lock()
    tasks = []

    for c_start, c_end in chunks:
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
        tasks.append(download_chunk(sem, url, payload, headers, symbol, all_candles, lock))

    await asyncio.gather(*tasks)

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

    print(f"  [SUCCESS] Saved {symbol} to {market_path} ({len(sorted_times)} records)")
    return True

async def main():
    nifty151_200_csv = backend_dir.parent / "market_data" / "history" / "nifty_151-200" / "nifty_151-200.csv"
    if not nifty151_200_csv.exists():
        print(f"ERROR: Nifty 151-200 symbols list not found at: {nifty151_200_csv}")
        sys.exit(1)
        
    market_dir = backend_dir.parent / "market_data" / "history" / "nifty_151-200"
    market_dir.mkdir(parents=True, exist_ok=True)
    
    # Read symbols
    symbols = []
    with open(nifty151_200_csv, mode="r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None) # skip header
        for row in reader:
            if row and row[0].strip():
                symbols.append(row[0].strip())
                
    print(f"Loaded {len(symbols)} symbols from CSV.")
    
    # Download Dhan scrip master to resolve IDs
    scrip_master_path = backend_dir.parent / "market_data" / "api-scrip-master.csv"
    if not scrip_master_path.exists():
        download_dhan_scrip_master(scrip_master_path)
        
    # Match symbols
    symbol_to_id = {}
    print("Matching symbols with Dhan Scrip Master...")
    with open(scrip_master_path, mode="r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        headers = next(reader, None)
        if headers:
            headers = [h.strip() for h in headers]
            try:
                exch_idx = headers.index("SEM_EXM_EXCH_ID")
                sec_idx = headers.index("SEM_SMST_SECURITY_ID")
                symbol_idx = headers.index("SEM_TRADING_SYMBOL")
                series_idx = headers.index("SEM_SERIES")
            except ValueError as ve:
                print(f"Error finding expected columns in headers: {headers}. Exception: {ve}")
                sys.exit(1)
                
            for row in reader:
                if len(row) > max(exch_idx, sec_idx, symbol_idx, series_idx):
                    exch = row[exch_idx].strip()
                    series = row[series_idx].strip()
                    symbol = row[symbol_idx].strip()
                    sec_id = row[sec_idx].strip()
                    
                    if exch == "NSE" and series == "EQ" and symbol in symbols:
                        symbol_to_id[symbol] = sec_id
                        
    print(f"Matched {len(symbol_to_id)} out of {len(symbols)} symbols.")
    
    # Save mapping
    mapping_csv = backend_dir.parent / "market_data" / "nifty151_200_security_ids.csv"
    with open(mapping_csv, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["symbol", "security_id"])
        for sym in sorted(symbol_to_id.keys()):
            writer.writerow([sym, symbol_to_id[sym]])
            
    print(f"Saved mappings to: {mapping_csv}")
    
    # Clean up master scrips file
    if scrip_master_path.exists():
        scrip_master_path.unlink()
        print("Cleaned up temporary api-scrip-master.csv")
        
    # Download 3 years data for matched symbols
    end_date = date(2026, 7, 6)
    start_date = end_date - timedelta(days=3 * 365) # 3 years
    
    stocks_to_download = sorted(symbol_to_id.keys())
    print(f"\nDownloading 3 years of data for {len(stocks_to_download)} stocks from {start_date} to {end_date}...\n")
    
    # Concurrency limit semaphore to respect Dhan APIs
    sem = asyncio.Semaphore(15)
    
    for i, symbol in enumerate(stocks_to_download):
        sec_id = symbol_to_id[symbol]
        print(f"[{i+1}/{len(stocks_to_download)}] Processing {symbol}...")
        success = await download_stock_data(symbol, sec_id, start_date, end_date, market_dir, sem)
        
        # Pause slightly between stocks to be polite
        if i < len(stocks_to_download) - 1:
            await asyncio.sleep(1.0)
            
    print("\nAll downloads finished successfully!")

if __name__ == "__main__":
    asyncio.run(main())
