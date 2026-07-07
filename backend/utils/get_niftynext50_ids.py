import os
import sys
import csv
import urllib.request
from pathlib import Path

# Setup paths
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

def download_niftynext50_list() -> list:
    url = "https://www.niftyindices.com/IndexConstituent/ind_niftynext50list.csv"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    symbols = []
    try:
        print(f"Fetching Nifty Next 50 constituents list from: {url}")
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            lines = [line.decode("utf-8") for line in response.readlines()]
            reader = csv.DictReader(lines)
            for row in reader:
                sym = row.get("Symbol")
                if sym:
                    symbols.append(sym.strip())
        print(f"Successfully fetched {len(symbols)} symbols from NiftyIndices.")
    except Exception as e:
        print(f"Failed to fetch Nifty Next 50 constituents dynamically: {e}")
        # Fallback to standard Nifty Next 50 stock symbols
        symbols = [
            "ABB", "ACC", "ADANIGREEN", "ADANIENSOL", "AMBUJACEM", "APARINDS", "ASTRAL", 
            "AVENUE", "BAJAJHLDNG", "BANKBARODA", "BERGEPAINT", "BOSCHLTD", "CANBK", 
            "CGPOWER", "CHOLAFIN", "COLPAL", "CONCOR", "DLF", "DMART", "GAIL", 
            "GICRE", "GODREJCP", "HAL", "HAVELLS", "INDHOTEL", "IOC", "IRFC", 
            "JINDALSTEL", "JIOFIN", "LICI", "LUPIN", "MARICO", "MRF", "MUTHOOTFIN", 
            "NHPC", "NMDC", "OBEROIRLTY", "PIDILITIND", "PFC", "PNB", "RECLTD", 
            "RVNL", "SAIL", "SHREECEM", "SIEMENS", "SRF", "TATACOMM", "TATAPOWER", 
            "TVSMOTOR", "ZOMATO"
        ]
        # De-duplicate and take top 50
        symbols = list(dict.fromkeys(symbols))[:50]
        print(f"Using fallback list of {len(symbols)} symbols.")
    return symbols

def download_dhan_scrip_master(output_path: Path):
    url = "https://images.dhan.co/api-data/api-scrip-master.csv"
    print(f"Downloading Dhan Scrip Master from: {url}")
    try:
        urllib.request.urlretrieve(url, output_path)
        print("Successfully downloaded Dhan Scrip Master.")
    except Exception as e:
        print(f"Error downloading Dhan Scrip Master: {e}")
        sys.exit(1)

def match_niftynext50_ids():
    output_dir = backend_dir.parent / "market_data"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    scrip_master_path = output_dir / "api-scrip-master.csv"
    if not scrip_master_path.exists():
        download_dhan_scrip_master(scrip_master_path)
        
    niftynext50_symbols = download_niftynext50_list()
    
    niftynext50_ids = []
    print(f"Parsing Scrip Master and matching Nifty Next 50 symbols...")
    with open(scrip_master_path, mode="r", encoding="utf-8", errors="ignore") as f:
        first_line = f.readline()
        headers = [h.strip() for h in first_line.split(",")]
        
        try:
            exch_idx = headers.index("SEM_EXM_EXCH_ID")
            sec_idx = headers.index("SEM_SMST_SECURITY_ID")
            symbol_idx = headers.index("SEM_TRADING_SYMBOL")
            series_idx = headers.index("SEM_SERIES")
        except ValueError as ve:
            print(f"Error finding expected columns in headers: {headers}. Exception: {ve}")
            sys.exit(1)
            
        for line in f:
            row = [val.strip() for val in line.split(",")]
            if len(row) > max(exch_idx, sec_idx, symbol_idx, series_idx):
                exch = row[exch_idx]
                series = row[series_idx]
                symbol = row[symbol_idx]
                sec_id = row[sec_idx]
                
                # Match NSE EQ equity instruments
                if exch == "NSE" and series == "EQ" and symbol in niftynext50_symbols:
                    niftynext50_ids.append({
                        "symbol": symbol,
                        "security_id": sec_id
                    })
                    
    niftynext50_ids.sort(key=lambda x: x["symbol"])
    
    niftynext_csv_path = output_dir / "niftynext50_security_ids.csv"
    with open(niftynext_csv_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["symbol", "security_id"])
        for item in niftynext50_ids:
            writer.writerow([item["symbol"], item["security_id"]])
            
    print(f"\nDone matching. Saved Nifty Next 50 security IDs to: {niftynext_csv_path}")
    print(f"Found matches for {len(niftynext50_ids)} out of {len(niftynext50_symbols)} symbols.")
    
    if scrip_master_path.exists():
        scrip_master_path.unlink()
        print("Cleaned up temporary api-scrip-master.csv file.")

if __name__ == "__main__":
    match_niftynext50_ids()
