#!/usr/bin/env python3
"""
約定 WebSocket リスナー
-----------------------
- 成行 TP / SL の fill を検知し、OrderManager.on_fill() へ伝播
"""

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
    def __init__(self, order_manager: OrderManager):
        self._om = order_manager

    async def _handler(self) -> None:
        async with websockets.connect(WS_ENDPOINT) as ws:
            # private 約定チャンネル購読
            sub_msg = {
                "method": "sub.personal.order",
                "param": {"symbol": self._om.symbol},
                "id": 1,
            }
            await ws.send(json.dumps(sub_msg))
            logger.info("✅ WS subscribed personal.order")

            async for msg in ws:
                data: Dict[str, Any] = json.loads(msg)
                if "data" not in data:
                    continue

                order_info = data["data"]
                status = order_info.get("state")  # 3 = filled
                if status == 3:
                    filled_id = str(order_info["orderId"])
                    self._om.on_fill(filled_id)

    def run_forever(self) -> None:
        asyncio.run(self._handler())
