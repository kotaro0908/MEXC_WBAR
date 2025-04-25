from __future__ import annotations
"""
fill_utils.py  –  helper routines for partial‑fill detection and timeout logic

このモジュールは OrderManager / OrderMonitor から呼び出して
エントリー注文の『部分約定の継続監視』や『タイムアウト判定』を一元管理する。
"""

from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger
from config.settings import settings

logger = get_logger(__name__)

###############################################################################
# 部分約定判定
###############################################################################

def is_partial_filled(order: dict, threshold: float | None = None) -> bool:
    """戻り値 True なら『部分約定』

    Parameters
    ----------
    order : dict
        ccxt.fetch_order() で得られる注文情報。
    threshold : float, optional
        fill ÷ amount が `threshold` 未満なら『partial』とみなす。
        None の場合は settings.POSITION_THRESHOLD を利用。
    """
    threshold = threshold or settings.POSITION_THRESHOLD
    amount = float(order.get("amount", 0))
    filled = float(order.get("filled", 0))
    if amount == 0:
        return False
    ratio = filled / amount
    logger.debug(f"[fill_utils] partial‑check amount={amount} filled={filled} ratio={ratio:.3f}")
    return 0.0 < ratio < threshold

###############################################################################
# タイムアウト判定
###############################################################################

def is_entry_timeout(order_iso: str, timeout_sec: int) -> bool:
    """エントリー注文が timeout 秒を経過したか判定"""
    if timeout_sec <= 0 or not order_iso:
        return False
    t0 = datetime.fromisoformat(order_iso)
    return (datetime.now(timezone.utc) - t0).total_seconds() > timeout_sec


###############################################################################
# 価格かい離チェック
###############################################################################

def is_price_far(current_price: float, entry_price: float, side: str, offset_entry: float) -> bool:
    """現在価格がエントリー価格からオフセット以上離れているか"""
    if current_price is None or entry_price is None:
        return False
    if side == "LONG":
        return current_price > entry_price + offset_entry
    else:
        return current_price < entry_price - offset_entry

###############################################################################
# エントリー注文継続可否判定
###############################################################################

def should_cancel_entry(order_mgr: "OrderManager", ccxt_order: dict, current_price: float) -> bool:
    """entry 注文をキャンセルすべきか判定"""
    # 1) timeout 超過
    if is_entry_timeout(order_mgr.order_timestamp, order_mgr.trade_logic.order_timeout_sec):
        logger.warning("[fill_utils] entry order timeout true")
        return True

    # 2) 部分約定で許容以上待機
    if is_partial_filled(ccxt_order):
        logger.info("[fill_utils] partial fill detected → keep alive")
        return False

    # 3) 価格かい離
    if is_price_far(current_price, float(ccxt_order.get("price", 0)), order_mgr.open_position_side, order_mgr.trade_logic.offset_pct):
        logger.warning("[fill_utils] price far from entry")
        return True
    return False
