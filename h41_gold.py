import requests
import json
import os
from datetime import datetime

API_KEY = "950d24880df05b9e609f2927d3a1076b"
BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

SERIES = {
    "reserve_balances": "WRESBAL",
    "reverse_repo": "RRPONTSYD",
    "liquidity_swaps": "SWPT",
    "total_assets": "WALCL",
    "treasury_account": "WTREGEN"
}

def fetch_fred(series_id, limit=2):
    params = {
        "series_id": series_id,
        "api_key": API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit
    }
    r = requests.get(BASE_URL, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    obs = data.get("observations", [])
    if len(obs) < 2:
        return None
    try:
        latest = float(obs[0]["value"]) if obs[0]["value"] != "." else 0
        prev = float(obs[1]["value"]) if obs[1]["value"] != "." else 0
        return {
            "date": obs[0]["date"],
            "latest": latest,
            "prev": prev,
            "change": latest - prev,
            "change_pct": ((latest - prev) / prev * 100) if prev != 0 else 0
        }
    except:
        return None

def get_xau_signal():
    results = {}
    signals = []

    for name, sid in SERIES.items():
        data = fetch_fred(sid)
        if not data:
            continue
        results[name] = data

        if name == "reserve_balances":
            if data["change"] > 0:
                signals.append(("reserve_balances", "BULLISH GOLD", f"+${data['change']:.0f}M liquidity IN"))
            else:
                signals.append(("reserve_balances", "BEARISH GOLD", f"{data['change']:.0f}M liquidity OUT"))

        if name == "reverse_repo":
            if data["change"] < 0:
                signals.append(("reverse_repo", "BULLISH GOLD", f"RRP down {data['change']:.0f}M - cash flooding"))
            else:
                signals.append(("reverse_repo", "BEARISH GOLD", f"RRP up {data['change']:.0f}M - cash parked"))

        if name == "liquidity_swaps":
            if data["latest"] > 100:
                signals.append(("liquidity_swaps", "BULLISH GOLD FEAR", f"SWPT {data['latest']:.1f}M - BANK STRESS!"))
            else:
                signals.append(("liquidity_swaps", "NEUTRAL", f"SWPT {data['latest']:.1f}M - no stress"))

        if name == "total_assets":
            if data["change"] > 0:
                signals.append(("total_assets", "BULLISH GOLD", f"Fed BS up +${data['change']:.0f}M - PRINTING"))
            else:
                signals.append(("total_assets", "BEARISH GOLD", f"Fed BS down {data['change']:.0f}M - QT"))

        if name == "treasury_account":
            if data["change"] < 0:
                signals.append(("treasury_account", "BULLISH GOLD", f"TGA down {data['change']:.0f}M - gov spending"))
            else:
                signals.append(("treasury_account", "BEARISH GOLD", f"TGA up +{data['change']:.0f}M - gov sucking liquidity"))

    bullish = len([s for s in signals if "BULLISH" in s[1]])
    bearish = len([s for s in signals if "BEARISH" in s[1]])

    if bullish >= 3:
        final = "BUY XAUUSD 🚀💛"
    elif bearish >= 3:
        final = "SELL XAUUSD 📉"
    else:
        final = "NEUTRAL / WAIT ⏳"

    output = {
        "timestamp": datetime.utcnow().isoformat(),
        "final_signal": final,
        "score": f"{bullish} bullish vs {bearish} bearish",
        "details": results,
        "signals": [{"factor": s[0], "signal": s[1], "reason": s[2]} for s in signals]
    }

    print(json.dumps(output, indent=2))
    with open("h41_gold_signal.json", "w") as f:
        json.dump(output, f, indent=2)
    return output

if __name__ == "__main__":
    get_xau_signal()