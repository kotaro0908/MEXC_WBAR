#!/usr/bin/env python3
"""
strategy.py
===========

WBARSimpleStrategy
------------------
* 2 本連続同方向のローソク足でエントリー
* TP / SL は OrderManager に丸投げ（擬似 OCO）

※ フィルタ（出来高スパイク / ATR など）は今は未実装。
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from .order_manager import BUY, SELL, OrderManager

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class WBARSimpleStrategy:
    """
    超シンプル実装:
      - ローソク足が同方向に 2 本連続したらエントリー
      - LONG なら次足始値で成行買い、TP/SL は RR1:1
    """

    def __init__(self, symbol: str, lot: str):
        self._om  = OrderManager(symbol)
        self.lot  = lot

        # 直近 2 本のバーを保持
        self._hist: List[dict] = []

        # TP/SL 差分 (%) を .env から読む
        self._offset_pct = float(os.getenv("OFFSET_PCT", "0.15"))

    # ------------------------------------------------------------------ #
    #  シグナル判定
    # ------------------------------------------------------------------ #
    def evaluate(self, bar: dict) -> Optional[str]:
        """
        Parameters
        ----------
        bar : dict
            {"ts": .., "open": .., "close": .., ...}

        Returns
        -------
        "LONG" / "SHORT" / None
        """
        self._hist.append(bar)
        if len(self._hist) < 2:
            return None
        if len(self._hist) > 2:
            self._hist.pop(0)

        b1, b2 = self._hist  # b1 = 1 本前, b2 = 最新
        up1  = b1["close"] > b1["open"]
        up2  = b2["close"] > b2["open"]
        dn1  = b1["close"] < b1["open"]
        dn2  = b2["close"] < b2["open"]

        if up1 and up2:
            return "LONG"
        if dn1 and dn2:
            return "SHORT"
        return None

    # ------------------------------------------------------------------ #
    #  エントリー & TP/SL キュー投入
    # ------------------------------------------------------------------ #
    def place_entry(self, direction: str) -> Optional[str]:
        """
        Parameters
        ----------
        direction : "LONG" または "SHORT"
        """
        if direction == "LONG":
            side     = BUY
            tp_side  = SELL
            sl_side  = SELL
        else:
            side     = SELL
            tp_side  = BUY
            sl_side  = BUY

        # 1) エントリー (成行 Market)
        entry_id = self._om.create_market_order(side=side, vol=self.lot)
        if not entry_id:
            logger.warning("Entry order failed.")
            return None

        # 2) TP / SL をキューに投入（擬似 OCO）
        self._om.queue_exit_market(
            entry_order_id=entry_id,
            tp_side=tp_side,
            sl_side=sl_side,
            vol=self.lot,
        )
        logger.info(f"Entry done. Queued TP/SL for {entry_id}")
        return entry_id
