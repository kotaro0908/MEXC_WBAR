#!/usr/bin/env python3
"""
主要ロジック抜粋 – エントリーと TP/SL キューのみ改修
"""

import logging
from typing import Optional

from .order_manager import BUY, SELL, OrderManager

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class WBARSimpleStrategy:
    def __init__(self, symbol: str, lot: str):
        self._om = OrderManager(symbol)
        self.lot = lot

    # ----------------------------------- #
    # Entry                               #
    # ----------------------------------- #

    def place_entry(self, direction: str) -> Optional[str]:
        """
        direction : "LONG" or "SHORT"
        """
        if direction == "LONG":
            side = BUY
            tp_side = SELL
            sl_side = SELL
        else:
            side = SELL
            tp_side = BUY
            sl_side = BUY

        # 1) entry
        entry_id = self._om.create_market_order(side=side, vol=self.lot)
        if not entry_id:
            return None

        # 2) exit queue（TP / SL）
        self._om.queue_exit_market(
            entry_order_id=entry_id,
            tp_side=tp_side,
            sl_side=sl_side,
            vol=self.lot,
        )
        logger.info(f"Entry done. Queued TP/SL for {entry_id}")
        return entry_id
