#!/usr/bin/env python3
"""
MEXC Futures 1m OHLCV ― 直近 5 本を取得して表示
依存: pip install requests
"""

import datetime as dt
import requests

SYMBOL   = "SOL_USDT"   # 先物シンボル
INTERVAL = "Min1"       # 1 分足
LIMIT    = 5            # 本数

url    = f"https://contract.mexc.com/api/v1/contract/kline/{SYMBOL}"
params = {"interval": INTERVAL, "limit": LIMIT}

r = requests.get(url, params=params, timeout=10)
print("HTTP", r.status_code)
print("URL :", r.url)
print("\n== raw json ==")
print(r.text[:200] + (" ..." if len(r.text) > 200 else ""))

if r.ok and r.headers.get("Content-Type", "").startswith("application/json"):
    for ts, o, h, l, c, v in zip(
        r.json()["data"]["time"],
        r.json()["data"]["open"],
        r.json()["data"]["high"],
        r.json()["data"]["low"],
        r.json()["data"]["close"],
        r.json()["data"]["vol"],
    ):
        t = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc)
        print(f"{t}  O:{o} H:{h} L:{l} C:{c} V:{v}")
