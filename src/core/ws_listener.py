#!/usr/bin/env python3
"""
ws_listener.py
==============

MEXC Futures 約定 WebSocket リスナー
-----------------------------------
* `sub.personal.order` チャンネルで自分の注文約定を監視
* TP / SL いずれか fill ⇒ OrderManager.on_fill(order_id) へ伝播
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict

import websockets

from .order_manager import OrderManager

WS_ENDPOINT = "wss://contract.mexc.com/edge"
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class WSListener:
    """約定通知を OrderManager へ橋渡しするだけの軽量クラス"""

    def __init__(self, order_manager: OrderManager):
        self._om = order_manager

    # ──────────────────────────
    #  内部ハンドラ（asyncio）
    # ──────────────────────────
    async def _handler(self) -> None:
        async with websockets.connect(WS_ENDPOINT, ping_interval=15) as ws:
            # private 約定チャンネル購読
            await ws.send(json.dumps({
                "method": "sub.personal.order",
                "param": {"symbol": self._om.symbol},
                "id": 1,
            }))
            logger.info("✅ WS subscribed personal.order")

            async for msg in ws:
                try:
                    data: Dict[str, Any] = json.loads(msg)
                except json.JSONDecodeError:
                    logger.debug(f"Skip non-JSON message: {msg[:80]} …")
                    continue

                raw = data.get("data")
                if raw is None:
                    continue

                # data["data"] が JSON 文字列で来るケースに対応
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.debug(f"Skip non-dict payload: {raw[:80]} …")
                        continue

                if not isinstance(raw, dict):
                    continue

                if raw.get("state") == 3:           # 3 = filled
                    filled_id = str(raw.get("orderId"))
                    if filled_id:
                        self._om.on_fill(filled_id)

    # ──────────────────────────
    #  外部呼び出し用
    # ──────────────────────────
    def run_forever(self) -> None:
        """Blocking 実行（別スレッド推奨）"""
        asyncio.run(self._handler())
