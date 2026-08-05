import os
import sys
import csv
import json
import time
import urllib.request
import pandas as pd
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

backend_dir = Path(__file__).resolve().parent
load_dotenv(dotenv_path=backend_dir / ".env")

access_token = os.getenv("ACCESS_TOKEN", "")

def fetch_5m_candles(security_id: str, exchange_segment: str, instrument: str, from_date: str, to_date: str):
    url = "https://api.dhan.co/v2/charts/intraday"
    headers = {
        "Content-Type": "application/json",
        "access-token": access_token
    }
    payload = {
        "securityId": str(security_id),
        "exchangeSegment": exchange_segment,
        "instrument": instrument,
        "expiryCode": 0,
        "oi": False,
        "interval": "5",
        "fromDate": from_date,
        "toDate": to_date
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            if "timestamp" in res_data and len(res_data["timestamp"]) > 0:
                times = res_data["timestamp"]
                # Convert timestamps to ISO string format e.g. YYYY-MM-DDTHH:MM:SS
                dt_list = [datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S") for ts in times]
                df = pd.DataFrame({
                    "timestamp": dt_list,
                    "open": res_data["open"],
                    "high": res_data["high"],
                    "low": res_data["low"],
                    "close": res_data["close"],
                    "volume": res_data["volume"]
                })
                return df
            else:
                return pd.DataFrame()
    except Exception as e:
        print(f"Error fetching {security_id}: {e}")
        return pd.DataFrame()

def load_mappings():
    mappings = {}
    market_data_dir = backend_dir.parent / "market_data"
    for file_name in os.listdir(market_data_dir):
        if file_name.endswith("_security_ids.csv"):
            path = market_data_dir / file_name
            with open(path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    symbol = row["symbol"].strip().upper()
                    sec_id = row["security_id"].strip()
                    mappings[symbol] = sec_id
    # Add indices manually
    mappings["NIFTY_50"] = "13"
    mappings["BANK_NIFTY"] = "25"
    return mappings

def find_file_recursive(directory, filename):
    for path in Path(directory).rglob(filename):
        return path
    return None

def main():
    if not access_token:
        print("Error: ACCESS_TOKEN not found in .env")
        return

    # Load mappings
    mappings = load_mappings()
    print(f"Loaded {len(mappings)} mappings.")

    # Load watchlist
    watchlist_path = backend_dir.parent / "market_data" / "orb" / "choosed_stocks.xlsx"
    watchlist_df = pd.read_excel(watchlist_path)
    symbols = [str(x).strip().upper() for x in watchlist_df.iloc[:, 0].dropna().tolist()]
    
    # Ensure NIFTY_50 and BANK_NIFTY are included
    if "NIFTY_50" not in symbols:
        symbols.append("NIFTY_50")
    if "BANK_NIFTY" not in symbols:
        symbols.append("BANK_NIFTY")

    print(f"Watchlist contains {len(symbols)} symbols to update.")

    history_dir = backend_dir.parent / "market_data" / "history"
    
    success_count = 0
    fail_count = 0

    for idx, symbol in enumerate(symbols):
        # Resolve target filename
        filename = f"{symbol}_3y_5m.csv"
        csv_path = find_file_recursive(history_dir, filename)
        
        if not csv_path:
            # Try to see if it's in the base directory
            csv_path = history_dir / filename
            if not csv_path.exists():
                print(f"[{idx+1}/{len(symbols)}] Skipping {symbol}: original CSV file not found.")
                fail_count += 1
                continue

        sec_id = mappings.get(symbol)
        if not sec_id:
            print(f"[{idx+1}/{len(symbols)}] Skipping {symbol}: security ID mapping not found.")
            fail_count += 1
            continue

        # Resolve segment/instrument
        exch = "IDX_I" if symbol in ("NIFTY_50", "BANK_NIFTY") else "NSE_EQ"
        inst = "INDEX" if symbol in ("NIFTY_50", "BANK_NIFTY") else "EQUITY"

        print(f"[{idx+1}/{len(symbols)}] Updating {symbol} via Dhan SecID {sec_id}...")

        # Fetch July 2026 data (extending to cover Aug 5 as well)
        new_df = fetch_5m_candles(sec_id, exch, inst, "2026-07-01", "2026-08-05")
        if new_df.empty:
            print(f"  No new candles returned from Dhan API.")
            continue

        # Read existing CSV
        try:
            old_df = pd.read_csv(csv_path)
        except Exception as e:
            print(f"  Error reading {csv_path}: {e}")
            continue

        # Add symbol column to new candles if needed
        new_df["symbol"] = symbol

        # Merge, drop duplicates on timestamp, and sort
        combined = pd.concat([old_df, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
        combined = combined.sort_values("timestamp")

        # Save back to CSV
        combined.to_csv(csv_path, index=False)
        print(f"  Merged! Total rows went from {len(old_df)} to {len(combined)}. Max timestamp now: {combined['timestamp'].max()}")
        success_count += 1
        
        # Throttling to respect Dhan API rate limits
        time.sleep(0.2)

    print(f"\nUpdate completed! Successfully updated: {success_count}, Failed/Skipped: {fail_count}")

if __name__ == "__main__":
    main()
