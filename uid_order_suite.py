#!/usr/bin/env python3
"""
uid_order_suite.py
──────────────────
MEXC Futures を UID (WEB… cookie) だけで操作できるか確認するスクリプト。

  A) 成行で建玉 → 数秒後に Market Close
  B) 指値で建玉 → 直後に Cancel

依存: curl-cffi, python-dotenv
       pip install curl-cffi python-dotenv
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from curl_cffi import requests
from dotenv import load_dotenv

# ──────────────────────────────────────────────
# .env 読み込み  (プロジェクト/config/.env を想定)
# ──────────────────────────────────────────────
load_dotenv(Path(__file__).parent / "config" / ".env")

# ────── 環境設定 ──────
UID     = os.getenv("UID") or os.getenv("MEXC_UID", "")
SYMBOL  = os.getenv("SYMBOL", "SOL_USDT")              # 例: "SOL_USDT"
LEV     = int(os.getenv("LEVERAGE", 20))
VOL     = float(os.getenv("VOL", 1))
BASE    = os.getenv("BASE_URL", "https://futures.mexc.com")

EP_CREATE = "/api/v1/private/order/create"
EP_CANCEL = "/api/v1/private/order/cancel"
# ─────────────────────

# ───────── Helper ─────────
def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _uid_sign(uid: str, payload) -> dict:
    """
    UID 署名ロジック（payload は dict でも list でも可）
    """
    ts = str(int(time.time() * 1000))
    g  = _md5(uid + ts)[7:]
    body = json.dumps(payload, separators=(",", ":"))
    sig = _md5(ts + body + g)
    return {"time": ts, "sign": sig}


def _headers(uid: str, sig: dict) -> dict:
    return {
        "Content-Type": "application/json",
        "x-mxc-sign":   sig["sign"],
        "x-mxc-nonce":  sig["time"],
        "User-Agent":   "Mozilla/5.0",
        "Authorization": uid,
    }


def _post(endpoint: str, body):
    sig = _uid_sign(UID, body)
    resp = requests.post(
        BASE + endpoint,
        json=body,
        headers=_headers(UID, sig),
        timeout=10,
    )
    return resp.json()
# ─────────────────────────

# ───────── TEST-A: Market open → Market close ─────────
def run_test_A() -> None:
    print("\n=== TEST-A  Market Open → Market Close ===")
    body_open = {
        "symbol": SYMBOL, "side": 1,          # open long
        "openType": 1,                        # isolated
        "type": 5,                            # market
        "vol": VOL, "leverage": LEV,
        "price": 0, "priceProtect": 0,
    }
    res_open = _post(EP_CREATE, body_open)
    print("OPEN  response:", json.dumps(res_open, indent=2, ensure_ascii=False))
    if not (res_open.get("success") and res_open.get("data")):
        return

    time.sleep(2)                             # 適宜調整

    body_close = {
        "symbol": SYMBOL, "side": 4,          # close long
        "openType": 1, "type": 5,
        "vol": VOL, "leverage": LEV,
        "price": 0, "priceProtect": 0,
    }
    res_close = _post(EP_CREATE, body_close)
    print("CLOSE response:", json.dumps(res_close, indent=2, ensure_ascii=False))


# ───────── TEST-B: Limit open → Cancel ─────────
def run_test_B() -> None:
    print("\n=== TEST-B  Limit Open → Cancel ===")
    body_limit = {
        "symbol": SYMBOL, "side": 3,          # open short
        "openType": 1, "type": 1,             # limit
        "vol": VOL, "leverage": LEV,
        "price": 999_999,                     # 約定しない遠値
        "priceProtect": 0,
    }
    res_limit = _post(EP_CREATE, body_limit)
    print("LIMIT response:", json.dumps(res_limit, indent=2, ensure_ascii=False))
    if not (res_limit.get("success") and res_limit.get("data")):
        return

    oid = str(res_limit["data"]["orderId"])

    time.sleep(1)

    # 公式仕様: cancel は「orderId の配列」そのものを POST する
    body_cancel = [oid]                       # ← キーを付けずリストで送信
    res_cancel  = _post(EP_CANCEL, body_cancel)
    print("CANCEL response:", json.dumps(res_cancel, indent=2, ensure_ascii=False))


# ───────── main ─────────
if __name__ == "__main__":
    if not UID.startswith("WEB"):
        sys.exit("‼  UID (WEB…) が未設定。環境変数 UID にセットしてください。")

    if len(sys.argv) != 2 or sys.argv[1] not in {"A", "B"}:
        print("usage: python uid_order_suite.py [A|B]")
        sys.exit(0)

    if sys.argv[1] == "A":
        run_test_A()
    else:
        run_test_B()
