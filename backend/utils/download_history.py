import os
import sys
import json
import csv
import urllib.request
import hashlib
import asyncio
from datetime import datetime, timedelta, date
from pathlib import Path
from dotenv import load_dotenv

# Setup paths
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

load_dotenv(dotenv_path=backend_dir / ".env")

def get_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def clean_timestamp(ts_val: float) -> datetime:
    # Convert epoch to datetime (IST timezone representation or UTC)
    return datetime.fromtimestamp(ts_val)

async def download_scrip_history(symbol: str, security_id: str, start_date: date, end_date: date) -> Path:
    print(f"\n--- Downloading History for {symbol} (Security ID: {security_id}) ---")
    url = "https://api.dhan.co/v2/charts/intraday"
    access_token = os.getenv("ACCESS_TOKEN", "")
    
    headers = {
        "Content-Type": "application/json",
        "access-token": access_token
    }

    # Split 180 days into 30-day chunks to respect Dhan API limits
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
        
        # Log exact API request parameters for the first chunk as proof
        if idx == 0:
            print(f"  Exact API Request Details:")
            print(f"    URL             : {url}")
            print(f"    Headers         : {{'Content-Type': 'application/json', 'access-token': '***'}}")
            print(f"    Payload         : {json.dumps(payload)}")
            print(f"    Instrument Name : EQUITY")
            print(f"    Exchange        : NSE_EQ")
            print(f"    Interval        : 1-minute")
            print(f"    Query Start Date: {start_date}")
            print(f"    Query End Date  : {end_date}")

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                
                # Verify keys and extract
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
            print(f"  Error fetching chunk {c_start} to {c_end}: {e}")

    # Sort candles by timestamp
    sorted_times = sorted(all_candles.keys())
    
    # Save directory setup
    out_dir = backend_dir.parent / "data" / "history"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Also write to market_data/history for backtest configuration convenience
    market_dir = backend_dir.parent / "market_data" / "history"
    market_dir.mkdir(parents=True, exist_ok=True)

    csv_name = f"{symbol}_180d.csv"
    out_path = out_dir / csv_name
    market_path = market_dir / csv_name

    # Write CSV
    with open(out_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "symbol", "open", "high", "low", "close", "volume"])
        for t_str in sorted_times:
            c = all_candles[t_str]
            writer.writerow([c["timestamp"], c["symbol"], c["open"], c["high"], c["low"], c["close"], c["volume"]])

    # Copy to market_data directory
    import shutil
    shutil.copy(out_path, market_path)

    # Compute metrics
    total_records = len(sorted_times)
    
    # Unique trading days present
    present_days = set(datetime.fromisoformat(t).date() for t in sorted_times)
    num_trading_days = len(present_days)

    # Calculate missing weekdays
    all_weekdays = []
    curr = start_date
    while curr <= end_date:
        if curr.weekday() < 5:  # Mon-Fri
            all_weekdays.append(curr)
        curr += timedelta(days=1)
    missing_days = [d for d in all_weekdays if d not in present_days]

    # Display results
    print(f"  Download Complete:")
    print(f"    Saved CSV Path         : {out_path}")
    print(f"    CSV Size (Bytes)       : {out_path.stat().st_size}")
    print(f"    SHA256 Checksum        : {get_sha256(out_path)}")
    print(f"    Number of Records      : {total_records}")
    print(f"    Number of Trading Days : {num_trading_days}")
    print(f"    Missing Trading Days   : {len(missing_days)} (e.g. {', '.join(d.strftime('%Y-%m-%d') for d in missing_days[:5])}...)")
    print(f"    Duplicate Timestamps   : {duplicate_count}")

    # Print first/last 10 rows
    print(f"  First 10 Rows:")
    for t_str in sorted_times[:10]:
        c = all_candles[t_str]
        print(f"    {c['timestamp']} | O:{c['open']:.2f} | H:{c['high']:.2f} | L:{c['low']:.2f} | C:{c['close']:.2f} | V:{c['volume']}")
    
    print(f"  Last 10 Rows:")
    for t_str in sorted_times[-10:]:
        c = all_candles[t_str]
        print(f"    {c['timestamp']} | O:{c['open']:.2f} | H:{c['high']:.2f} | L:{c['low']:.2f} | C:{c['close']:.2f} | V:{c['volume']}")

    return out_path

async def verify_datasets(path_sbin: Path, path_reliance: Path) -> None:
    print("\n================ VERIFYING DATASETS ================")
    
    def read_dataset_stats(file_path: Path):
        rows = []
        with open(file_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append({
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                    "timestamp": row["timestamp"]
                })
        return rows

    sbin_data = read_dataset_stats(path_sbin)
    reliance_data = read_dataset_stats(path_reliance)

    # Counts
    len_sbin = len(sbin_data)
    len_reliance = len(reliance_data)
    
    sha_sbin = get_sha256(path_sbin)
    sha_reliance = get_sha256(path_reliance)

    # Averages
    avg_close_sbin = sum(r["close"] for r in sbin_data) / len_sbin if len_sbin else 0
    avg_close_reliance = sum(r["close"] for r in reliance_data) / len_reliance if len_reliance else 0

    avg_vol_sbin = sum(r["volume"] for r in sbin_data) / len_sbin if len_sbin else 0
    avg_vol_reliance = sum(r["volume"] for r in reliance_data) / len_reliance if len_reliance else 0

    # Extremes
    max_high_sbin = max(r["high"] for r in sbin_data) if sbin_data else 0
    max_high_reliance = max(r["high"] for r in reliance_data) if reliance_data else 0

    min_low_sbin = min(r["low"] for r in sbin_data) if sbin_data else 0
    min_low_reliance = min(r["low"] for r in reliance_data) if reliance_data else 0

    date_range_sbin = f"{sbin_data[0]['timestamp']} to {sbin_data[-1]['timestamp']}" if sbin_data else "None"
    date_range_reliance = f"{reliance_data[0]['timestamp']} to {reliance_data[-1]['timestamp']}" if reliance_data else "None"

    print(f"Metric                 | SBIN                     | RELIANCE")
    print(f"--------------------------------------------------------------------------------")
    print(f"Row Count              | {len_sbin:<24d} | {len_reliance:<24d}")
    print(f"SHA256 Checksum        | {sha_sbin[:16]}...     | {sha_reliance[:16]}...")
    print(f"Average Close          | Rs.{avg_close_sbin:<20.2f} | Rs.{avg_close_reliance:<20.2f}")
    print(f"Average Volume         | {avg_vol_sbin:<24.1f} | {avg_vol_reliance:<24.1f}")
    print(f"Max High               | Rs.{max_high_sbin:<20.2f} | Rs.{max_high_reliance:<20.2f}")
    print(f"Min Low                | Rs.{min_low_sbin:<20.2f} | Rs.{min_low_reliance:<20.2f}")
    print(f"Date Range             | {date_range_sbin:<24} | {date_range_reliance:<24}")

    # Fail-safe identity check
    if len_sbin == len_reliance and sha_sbin == sha_reliance:
        raise ValueError("CRITICAL BUG: Downloaded datasets for SBIN and RELIANCE are identical! Stopping execution.")
    else:
        print("\n  [SUCCESS] Datasets are verified and are completely independent.")
    print("====================================================")

async def main():
    end_date = date(2026, 7, 5)
    start_date = end_date - timedelta(days=180)
    
    path_sbin = await download_scrip_history("SBIN", "3045", start_date, end_date)
    path_reliance = await download_scrip_history("RELIANCE", "2885", start_date, end_date)

    await verify_datasets(path_sbin, path_reliance)

if __name__ == "__main__":
    asyncio.run(main())
