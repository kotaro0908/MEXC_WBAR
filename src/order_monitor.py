from __future__ import annotations
"""
order_monitor.py – ENTRY / TP / SL の約定監視とマーチン状態更新
"""

import math
from typing import TYPE_CHECKING

from config.settings import settings
from utils.logger import get_logger
from utils.log_utils import log_json, log_trade_result

if TYPE_CHECKING:          # 型チェック用（循環 import 回避）
    from src.core.order_manager import OrderManager

logger = get_logger(__name__)


class OrderMonitor:
    def __init__(self, om: "OrderManager", notifier: "Notifier | None" = None):
        self.om = om
        self.notifier = notifier

    # =========================== public ==========================
    async def run(self) -> None:
        await self._check_entry()
        await self._check_tp()
        await self._check_sl()

    # ========================= internal ==========================
    async def _check_entry(self):
        if not self.om.entry_order_id:
            return
        status, price = self.om._check_order_filled_retry(self.om.entry_order_id)
        if status == "closed":
            self.om.entry_price = price or 0.0
            logger.info(f"[ENTRY] {self.om.open_position_side} @ {price}")
            if self.notifier:
                await self.notifier.send("INFO", f"ENTRY {self.om.open_position_side} {price}")
            log_json("ENTRY_FILLED", {"trade_id": self.om.current_trade_id,
                                      "side": self.om.open_position_side,
                                      "price": price,
                                      "size": self.om.dynamic_lot})
            self.om.entry_order_id = None
            self.om._save_trade_state()
        elif status == "canceled":
            logger.warning("Entry order canceled")
            self.om.entry_order_id = None

    async def _check_tp(self):
        finished = []
        for size, oid in list(self.om.tp_order_ids.items()):
            st, px = self.om._check_order_filled_retry(oid)
            if st != "closed":
                continue
            pnl = px - self.om.entry_price if self.om.open_position_side == "LONG" else self.om.entry_price - px
            logger.info(f"[TP] {px} pnl={pnl}")
            if self.notifier:
                await self.notifier.send("INFO", f"TP hit  pnl={pnl:.4f}")
            log_trade_result({"trade_id": self.om.current_trade_id,
                              "exit_type": "TP", "exit_price": px, "pnl": pnl,
                              "current_lot_size": self.om.dynamic_lot,
                              "direction": self.om.open_position_side})
            finished.append(size)

            # reset martingale
            self.om.consec_losses = 0
            self.om.dynamic_lot = self.om.base_lot
            if size in self.om.sl_order_ids:
                self.om._cancel_orders([self.om.sl_order_ids.pop(size)])
            self.om._save_trade_state()

        for s in finished:
            self.om.tp_order_ids.pop(s, None)
        if finished:
            self.om.open_position_side = None

    async def _check_sl(self):
        finished = []
        for size, oid in list(self.om.sl_order_ids.items()):
            st, px = self.om._check_order_filled_retry(oid)
            if st != "closed":
                continue
            pnl = px - self.om.entry_price if self.om.open_position_side == "LONG" else self.om.entry_price - px
            logger.warning(f"[SL] {px} pnl={pnl}")
            if self.notifier:
                await self.notifier.send("WARN", f"SL hit  pnl={pnl:.4f}")
            log_trade_result({"trade_id": self.om.current_trade_id,
                              "exit_type": "SL", "exit_price": px, "pnl": pnl,
                              "current_lot_size": self.om.dynamic_lot,
                              "direction": self.om.open_position_side})
            finished.append(size)

            # martingale step-up
            self.om.consec_losses += 1
            self.om.dynamic_lot = math.ceil(self.om.dynamic_lot * self.om.martingale_factor)

            if self.om.consec_losses >= settings.MAX_CONSECUTIVE_LOSSES:
                logger.error("Max consecutive losses reached – martingale reset")
                if self.notifier:
                    await self.notifier.send("ERROR", "Max consecutive losses – BOT reset")
                self.om.reset_martingale()
            else:
                self.om._save_trade_state()

            if size in self.om.tp_order_ids:
                self.om._cancel_orders([self.om.tp_order_ids.pop(size)])

        for s in finished:
            self.om.sl_order_ids.pop(s, None)
        if finished:
            self.om.open_position_side = None
