import urllib.request
import json
import os
from datetime import datetime

COT_URL = "https://www.cftc.gov/dea/newcot/f_disagg.txt"
GOLD_KEY = "GOLD - COMMODITY EXCHANGE"
OUTPUT_JSON = "gold_cot_xauusd.json"

def get_cot_lines():
    try:
        with urllib.request.urlopen(COT_URL, timeout=10) as r:
            data = r.read().decode('utf-8', errors='ignore')
        return data.splitlines()
    except Exception as e:
        if os.path.exists("f_disagg.txt"):
            with open("f_disagg.txt", "r", encoding="utf-8", errors="ignore") as f:
                return f.read().splitlines()
        raise e

def extract_gold_only():
    lines = get_cot_lines()
    gold_line = None
    for line in lines:
        if GOLD_KEY in line.upper():
            gold_line = line
            break
    if not gold_line:
        raise ValueError("GOLD not found in COT file!")
    parts = [p.strip() for p in gold_line.split(',')]
    try:
        result = {
            "symbol": "XAUUSD",
            "source": "GOLD - COMMODITY EXCHANGE INC.",
            "date": parts[2] if len(parts) > 2 else "",
            "timestamp": datetime.now().isoformat(),
            "raw": gold_line,
            "prod_merc_long": int(parts[7].strip() or 0),
            "prod_merc_short": int(parts[8].strip() or 0),
            "swap_long": int(parts[9].strip() or 0),
            "swap_short": int(parts[10].strip() or 0),
            "managed_money_long": int(parts[11].strip() or 0),
            "managed_money_short": int(parts[12].strip() or 0),
            "other_reportable_long": int(parts[13].strip() or 0),
            "other_reportable_short": int(parts[14].strip() or 0),
            "open_interest": int(parts[21].strip() or 0) if len(parts) > 21 else 0,
            "signal": {}
        }
        mm_net = result["managed_money_long"] - result["managed_money_short"]
        result["signal"]["managed_money_net"] = mm_net
        result["signal"]["bias"] = "BULLISH" if mm_net > 0 else "BEARISH" if mm_net < 0 else "NEUTRAL"
        result["signal"]["strength"] = abs(mm_net)
        with open(OUTPUT_JSON, "w") as jf:
            json.dump(result, jf, indent=2)
        return result
    except Exception as e:
        result = {
            "symbol": "XAUUSD",
            "raw": gold_line,
            "error_parse": str(e),
            "timestamp": datetime.now().isoformat()
        }
        with open(OUTPUT_JSON, "w") as jf:
            json.dump(result, jf, indent=2)
        return result

if __name__ == "__main__":
    data = extract_gold_only()
    print(f"GOLD ONLY EXTRACTED")
    print(json.dumps(data, indent=2))
    print(f"Saved: {OUTPUT_JSON}")