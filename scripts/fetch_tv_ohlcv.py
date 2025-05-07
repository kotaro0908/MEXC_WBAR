#!/usr/bin/env python3
"""
TradingView 非公開 "history" API で
MEXC Futures SOL/USDT:USDT の 1 分足を最大 90 日取得して CSV 保存。

* 30 日ごとに 3 バッチ取得
* tvc2〜tvc10 を順に試して 403/429 を自動回避
* User-Agent / Referer / Origin ヘッダーを付与
"""

import csv
import datetime as dt
import time
from pathlib import Path

import requests

# ------------------ 取得設定 ------------------ #
SYMBOL = "SOLUSDT.P"          # TV 先物シンボル。SOL/USDT:USDT は ".P" が付く
RESOLUTION = "1"              # 1 分足
PERIOD_DAYS = 30
TOTAL_DAYS = 90
OUT_CSV = Path("src/data/ohlcv_1m.csv")

TV_HOSTS = [f"https://tvc{i}.forexpros.com" for i in range(2, 11)]
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://jp.tradingview.com/",
    "Origin": "https://jp.tradingview.com",
}
RATE_LIMIT_SEC = 1.5          # Cloudflare ブロックを避ける待機

# --------------------------------------------- #
def fetch_chunk(host: str, symbol: str, res: str, frm: int, to: int):
    """単一 30 日チャンクを取得。失敗すると None を返す"""
    url = f"{host}/58c954110f02e3f7b8b54dcb9cdfc28e/1675667223/56/56/43/history"
    params = {"symbol": symbol, "resolution": res, "from": frm, "to": to}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        if r.status_code in (403, 429):
            return None          # 次のホストへ
        r.raise_for_status()
        data = r.json()
        if data.get("s") != "ok":
            return None
        return list(zip(data["t"], data["o"], data["h"], data["l"], data["c"], data["v"]))
    except Exception:
        return None

def fetch_history():
    end_ts = int(time.time())
    all_rows = []

    for i in range(TOTAL_DAYS // PERIOD_DAYS):
        start_ts = end_ts - PERIOD_DAYS * 24 * 60 * 60
        print(f"[Chunk {i+1}] {dt.datetime.utcfromtimestamp(start_ts)} → {dt.datetime.utcfromtimestamp(end_ts)}")

        # ホストを順に試す
        rows = None
        for host in TV_HOSTS:
            rows = fetch_chunk(host, SYMBOL, RESOLUTION, start_ts, end_ts)
            if rows:
                print(f"  ✓ {host.split('//')[1]} 取得 {len(rows)} 本")
                break
            else:
                print(f"  ✗ {host.split('//')[1]} 403/500 で失敗")
                time.sleep(0.5)

        if not rows:
            print("  どのホストでも取得できませんでした → 中断")
            break

        all_rows = rows + all_rows
        end_ts = start_ts           # 次はさらに過去へ
        time.sleep(RATE_LIMIT_SEC)  # レート制限

    print(f"\nTotal rows fetched: {len(all_rows)}")
    return all_rows

def save_csv(rows):
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["datetime", "open", "high", "low", "close", "volume"])
        for ts, o, h, l, c, v in rows:
            w.writerow([dt.datetime.utcfromtimestamp(ts), o, h, l, c, v])
    print("Saved →", OUT_CSV)

def main():
    rows = fetch_history()
    if rows:
        save_csv(rows)
    else:
        print("取得ゼロ行のため CSV 保存をスキップしました。")

if __name__ == "__main__":
    main()
