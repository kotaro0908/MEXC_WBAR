#!/usr/bin/env python3
"""
OrderManager  ―  純成行（type = 5）専用の注文管理クラス
-----------------------------------------------------------------
* エントリー        : create_market_order()
* TP / SL キュー    : queue_exit_market()
* 疑似 OCO          : WebSocket 側から on_fill() で残注文を cancel
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional

from curl_cffi import requests
from dotenv import load_dotenv

# .env 読み込み（プロジェクトルートから辿る想定）
ENV_PATH = os.path.join(os.path.dirname(__file__), "../../config/.env")
load_dotenv(dotenv_path=ENV_PATH)

# --- 環境変数 ---
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
UID = os.getenv("UID")  # UID 署名用
MEXC_CONTRACT_BASE_URL = os.getenv("MEXC_CONTRACT_BASE_URL")  # 例: https://contract.mexc.com

# --- ロギング ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --- 定数 ---
ENDPOINT_ORDER_CREATE = "/api/v1/private/order/submit"
ENDPOINT_ORDER_CANCEL = "/api/v1/private/order/cancel"

ORDER_TYPE_MARKET: str = "5"  # 成行
BUY: int = 1
SELL: int = 2


# ------------------------------------------------------------------ #
# 署名生成ヘルパ                                                     #
# ------------------------------------------------------------------ #
def _uid_sign(body: dict) -> dict:
    """
    UID を使った MEXC 特有の署名生成

    Returns
    -------
    dict
        {"time": "...", "sign": "..."} を返す
    """
    import hashlib

    ts = str(int(time.time() * 1000))
    g = hashlib.md5((UID + ts).encode("utf-8")).hexdigest()[7:]
    s = json.dumps(body, separators=(",", ":"))
    sign = hashlib.md5((ts + s + g).encode("utf-8")).hexdigest()
    return {"time": ts, "sign": sign}


# ------------------------------------------------------------------ #
# メインクラス                                                       #
# ------------------------------------------------------------------ #
class OrderManager:
    """
    - すべて MARKER ORDER (type 5) 固定
    - TP / SL は queue_exit_market() で内部保持
    - WebSocket 側から on_fill() をコール → 疑似 OCO
    """

    def __init__(self, symbol: str, leverage: int = 20) -> None:
        self.symbol = symbol
        self.leverage = leverage

        # orderId → {"tp_id": ..., "sl_id": ...}
        self._exit_map: Dict[str, Dict[str, str]] = {}

        # exit キュー（TP/SL 市場注文用）
        self._exit_queue: Deque[dict] = deque()
        self._queue_lock = threading.Lock()

        # Exit キュー処理用スレッド
        self._exit_worker = threading.Thread(
            target=self._process_exit_queue, name="ExitWorker", daemon=True
        )
        self._exit_worker.start()

    # ------------------------------ #
    # Public API                     #
    # ------------------------------ #

    def create_market_order(
        self, side: int, vol: str, open_type: int = 1
    ) -> Optional[str]:
        """
        純成行エントリー

        Parameters
        ----------
        side : int
            1 = Buy, 2 = Sell
        vol : str
            取引数量 (string)
        open_type : int, default=1
            1 = Isolated, 2 = Cross （API 仕様に準拠）

        Returns
        -------
        str | None
            成功時: orderId, 失敗時: None
        """
        body = {
            "symbol": self.symbol,
            "side": side,
            "type": ORDER_TYPE_MARKET,
            "openType": open_type,
            "leverage": self.leverage,
            "vol": vol,
        }
        body.update(_uid_sign(body))

        url = MEXC_CONTRACT_BASE_URL + ENDPOINT_ORDER_CREATE
        try:
            resp = requests.post(url, json=body, timeout=10)
            data = resp.json()
            if data.get("success") and data.get("code") == 0:
                order_id = str(data["data"])
                logger.info(f"✅ Market entry sent: {order_id}")
                return order_id
            logger.error(f"❌ ENTRY FAIL: {data}")
        except Exception as exc:
            logger.exception(f"ENTRY EXCEPTION: {exc}")
        return None

    def queue_exit_market(
        self,
        entry_order_id: str,
        tp_side: int,
        sl_side: int,
        vol: str,
    ) -> None:
        """
        TP/SL 市場注文をキューに登録し、OCO 監視マップに紐づけ

        Notes
        -----
        * side は entry と逆方向になるよう呼び出し側で渡す
        """
        # キュー投入用 dict
        tp_payload = {
            "symbol": self.symbol,
            "side": tp_side,
            "type": ORDER_TYPE_MARKET,
            "openType": 2,  # ポジション決済＝CLOSE(2)
            "leverage": self.leverage,
            "vol": vol,
        }
        sl_payload = {
            "symbol": self.symbol,
            "side": sl_side,
            "type": ORDER_TYPE_MARKET,
            "openType": 2,
            "leverage": self.leverage,
            "vol": vol,
        }

        with self._queue_lock:
            self._exit_queue.append(tp_payload)
            self._exit_queue.append(sl_payload)
            # キューに積んだ順に orderId が返る想定
            self._exit_map[entry_order_id] = {"tp_id": None, "sl_id": None}

    def on_exit_order_created(
        self, tp_id: str, sl_id: str, entry_order_id: str
    ) -> None:
        """キューから exit 送信後、実際の orderId をマッピング"""
        self._exit_map[entry_order_id]["tp_id"] = tp_id
        self._exit_map[entry_order_id]["sl_id"] = sl_id

    def on_fill(self, filled_order_id: str) -> None:
        """
        WebSocket で fill 通知を受信したら呼ぶ

        - TP が先に約定 → SL をキャンセル
        - SL が先に約定 → TP をキャンセル
        """
        for entry_id, exit_dict in list(self._exit_map.items()):
            tp_id = exit_dict.get("tp_id")
            sl_id = exit_dict.get("sl_id")

            if filled_order_id == tp_id and sl_id:
                self.cancel_order(sl_id)
                logger.info(f"OCO: TP filled ({tp_id}), SL {sl_id} cancelled")
                del self._exit_map[entry_id]

            elif filled_order_id == sl_id and tp_id:
                self.cancel_order(tp_id)
                logger.info(f"OCO: SL filled ({sl_id}), TP {tp_id} cancelled")
                del self._exit_map[entry_id]

    def cancel_order(self, order_id: str) -> bool:
        """単一注文をキャンセル"""
        body = {"orderId": order_id}
        body.update(_uid_sign(body))

        url = MEXC_CONTRACT_BASE_URL + ENDPOINT_ORDER_CANCEL
        try:
            resp = requests.post(url, json=body, timeout=10)
            data = resp.json()
            if data.get("success"):
                logger.info(f"🛑 CANCELED {order_id}")
                return True
            logger.error(f"❌ CANCEL FAIL: {data}")
        except Exception as exc:
            logger.exception(f"CANCEL EXCEPTION: {exc}")
        return False

    # ------------------------------ #
    # Internal Worker                #
    # ------------------------------ #

    def _process_exit_queue(self) -> None:
        """バックグラウンドで exit キューを送信し続ける"""
        while True:
            try:
                with self._queue_lock:
                    if not self._exit_queue:
                        time.sleep(0.1)
                        continue
                    payload = self._exit_queue.popleft()

                payload.update(_uid_sign(payload))
                url = MEXC_CONTRACT_BASE_URL + ENDPOINT_ORDER_CREATE
                resp = requests.post(url, json=payload, timeout=10)
                data = resp.json()

                if data.get("success") and data.get("code") == 0:
                    logger.info(f"➡️  Exit order sent: {data['data']}")
                else:
                    logger.error(f"❌ EXIT SEND FAIL: {data}")

            except Exception as exc:
                logger.exception(f"EXIT QUEUE EXCEPTION: {exc}")
            time.sleep(0.05)
