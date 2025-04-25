from __future__ import annotations

"""
order_monitor.py  –  monitors entry / TP / SL orders and updates martingale state

* 依存:
    - OrderManager が以下の属性 / メソッドを持つこと
        entry_order_id : str | None
        tp_order_ids   : dict[float, str]
        sl_order_ids   : dict[float, str]
        open_position_side : str | None  # "LONG" | "SHORT"
        dynamic_lot    : float
        consec_losses  : int
        base_lot       : float
        _check_order_filled_retry(order_id, max_retries=3, sleep_sec=2) -> tuple[str, float | None]
        _cancel_orders(order_ids: list[str])
        _save_trade_state()
        reset_martingale()
        tick             : float  # price precision (for logs only)
* 環境:
    - settings に MAX_CONSECUTIVE_LOSSES がある前提
"""

import asyncio
import math
from datetime import datetime, timezone

from config.settings import settings
from utils.logger import get_logger
from utils.log_utils import log_json, log_trade_result

logger = get_logger(__name__)


class OrderMonitor:
    """非同期に呼び出される TP / SL 監視ループ"""

    def __init__(self, om: "OrderManager"):
        self.om = om  # 循環 import 回避のためヒント文字列

    # ------------------------------------------------------------
    # public
    # ------------------------------------------------------------
    async def run(self):
        """外部から定期的に await されるメソッド"""
        await self._check_entry()
        await self._check_tp()
        await self._check_sl()

    # ------------------------------------------------------------
    # internal
    # ------------------------------------------------------------
    async def _check_entry(self):
        if not self.om.entry_order_id:
            return
        status, filled = self.om._check_order_filled_retry(self.om.entry_order_id)
        if status == "closed":
            # 約定確定 – entry price 保存 & 永続化
            self.om.entry_price = filled or 0.0
            logger.info(
                f"[ENTRY FILLED] side={self.om.open_position_side} price={filled} size={self.om.dynamic_lot}"
            )
            log_json(
                "ENTRY_FILLED",
                {
                    "trade_id": self.om.current_trade_id,
                    "side": self.om.open_position_side,
                    "price": filled,
                    "size": self.om.dynamic_lot,
                },
            )
            self.om.entry_order_id = None
            self.om._save_trade_state()
        elif status == "canceled":
            logger.warning("Entry order got canceled – clearing state")
            self.om.entry_order_id = None
            # no position, no martingale change

    async def _check_tp(self):
        to_remove: list[float] = []
        for size, oid in list(self.om.tp_order_ids.items()):
            status, price = self.om._check_order_filled_retry(oid)
            if status != "closed":
                continue
            pnl = (price - self.om.entry_price) if self.om.open_position_side == "LONG" else (self.om.entry_price - price)
            logger.info(f"[TP] filled size={size} price={price} PNL={pnl}")
            log_trade_result(
                {
                    "trade_id": self.om.current_trade_id,
                    "exit_type": "TP",
                    "exit_price": price,
                    "pnl": pnl,
                    "current_lot_size": self.om.dynamic_lot,
                    "direction": self.om.open_position_side,
                }
            )
            to_remove.append(size)
            # リセット martingale
            self.om.consec_losses = 0
            self.om.dynamic_lot = self.om.base_lot
            # キャンセル残 SL
            if size in self.om.sl_order_ids:
                self.om._cancel_orders([self.om.sl_order_ids.pop(size)])
            # save
            self.om._save_trade_state()
        # cleanup dict
        for s in to_remove:
            self.om.tp_order_ids.pop(s, None)
            self.om.open_position_side = None  # 状態クリア

    async def _check_sl(self):
        to_remove: list[float] = []
        for size, oid in list(self.om.sl_order_ids.items()):
            status, price = self.om._check_order_filled_retry(oid)
            if status != "closed":
                continue
            pnl = (price - self.om.entry_price) if self.om.open_position_side == "LONG" else (self.om.entry_price - price)
            logger.info(f"[SL] filled size={size} price={price} PNL={pnl}")
            log_trade_result(
                {
                    "trade_id": self.om.current_trade_id,
                    "exit_type": "SL",
                    "exit_price": price,
                    "pnl": pnl,
                    "current_lot_size": self.om.dynamic_lot,
                    "direction": self.om.open_position_side,
                }
            )
            to_remove.append(size)
            # martingale++
            self.om.consec_losses += 1
            self.om.dynamic_lot = self.om._next_lot()
            if self.om.consec_losses >= settings.MAX_CONSECUTIVE_LOSSES:
                logger.error("Max consecutive losses reached – resetting martingale & stopping bot")
                self.om.reset_martingale()
            else:
                self.om._save_trade_state()
            # キャンセル残 TP
            if size in self.om.tp_order_ids:
                self.om._cancel_orders([self.om.tp_order_ids.pop(size)])
        # cleanup dict
        for s in to_remove:
            self.om.sl_order_ids.pop(s, None)
            self.om.open_position_side = None
