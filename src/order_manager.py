import os
import json
import time
import math
import asyncio
from datetime import datetime, timezone

import ccxt
from curl_cffi import requests

from config.auth import generate_signature
from config.settings import settings
from utils.logger import get_logger
from utils.log_utils import log_json, log_trade_result

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# helpers : precision rounding
# ---------------------------------------------------------------------------

def round_to_tick(price: float, tick: float) -> float:
    """round down price to exchange tick size"""
    return math.floor(price / tick) * tick


def round_to_qty(qty: float, min_qty: float) -> float:
    """round down qty to exchange minQty"""
    steps = math.floor(qty / min_qty)
    return steps * min_qty


# ---------------------------------------------------------------------------
# OrderManager
# ---------------------------------------------------------------------------


class OrderManager:
    """Handles entry / TP / SL orders and martingale sizing"""

    # -------------------- init --------------------
    def __init__(
        self,
        trade_logic,
        ccxt_symbol: str,
        ws_symbol: str,
        lot_size: float,
        leverage: int,
        uid: str,
        api_key: str,
        api_secret: str,
        position_threshold: float = 0.95,
        monitor=None,
    ):
        self.trade_logic = trade_logic  # Strategy instance
        self.monitor = monitor

        # symbol / exchange setup
        self.ccxt_symbol = ccxt_symbol
        self.ws_symbol = ws_symbol
        self.base_lot = lot_size
        self.leverage = leverage
        self.uid = uid
        self.position_thr = position_threshold

        self.exchange = ccxt.mexc(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "options": {"defaultType": "future", "recvWindow": 60000},
                "enableRateLimit": True,
            }
        )

        # precision info
        market_info = self.exchange.fetch_markets()
        m = next(m for m in market_info if m["symbol"] == self.ccxt_symbol)
        self.tick = m["precision"].get("price", 0.001) or 0.001
        self.min_qty = m["limits"]["amount"].get("min", 0.001) or 0.001
        logger.info(f"[PRECISION] tick={self.tick}, minQty={self.min_qty}")

        # martingale state
        self.dynamic_lot = lot_size
        self.consec_losses = 0

        # runtime vars
        self.open_position_side = None
        self.entry_order_id = None
        self.tp_order_ids = {}
        self.sl_order_ids = {}
        self.current_trade_id = None
        self.last_trade_time = time.time()
        self.order_lock_until = 0
        self.order_timestamp = None
        self.entry_price = None
        self.current_market_price = None

        # endpoints
        self.ORDER_URL = "https://futures.mexc.com/api/v1/private/order/submit"
        self.CANCEL_URL = "https://futures.mexc.com/api/v1/private/order/cancel"

        # persistence
        if settings.PERSISTENCE_ENABLED:
            self._restore_trade_state()

    # -------------------- utils --------------------
    def _next_lot(self) -> float:
        raw = self.base_lot * (settings.MARTIN_FACTOR ** self.consec_losses)
        return round_to_qty(raw, self.min_qty)

    def update_market_price(self, price: float):
        self.current_market_price = price

    # -------------------- position / order check --------------------
    def has_open_position_or_order(self) -> bool:
        if time.time() < self.order_lock_until:
            return True
        try:
            positions = self.exchange.fetch_positions([self.ccxt_symbol])
            if any(abs(float(p.get("contracts", 0))) > 0.0001 for p in positions):
                return True
        except Exception as e:
            logger.error(f"Position check error: {e}")
            return True
        if self.entry_order_id or self.tp_order_ids or self.sl_order_ids:
            return True
        return False

    # -------------------- ENTRY --------------------
    async def place_entry_order(self, side: str, trigger_price: float, trade_info: dict | None = None):
        if self.has_open_position_or_order():
            return

        self.dynamic_lot = self._next_lot()
        if self.dynamic_lot < self.min_qty:
            logger.warning("Lot below minQty, skip")
            return

        sl_offset = self.trade_logic.calc_offset(trigger_price)
        sl_price = trigger_price - sl_offset if side == "LONG" else trigger_price + sl_offset
        sl_price = round_to_tick(sl_price, self.tick)

        order_side = 1 if side == "LONG" else 3
        params = {
            "symbol": self.ws_symbol,
            "side": order_side,
            "openType": 2,
            "type": "6",
            "vol": str(self.dynamic_lot),
            "leverage": self.leverage,
            "priceProtect": "0",
            "stopLossPrice": f"{sl_price}",
        }

        resp = self._place_order(params)
        if not (resp and resp.get("success")):
            logger.error(f"Entry order failed: {resp}")
            return

        self.entry_order_id = resp["data"]
        self.open_position_side = side
        self.order_timestamp = datetime.utcnow().isoformat()
        self.current_trade_id = self.current_trade_id or f"T{datetime.utcnow():%Y%m%d_%H%M%S}"
        self.order_lock_until = time.time() + 5

        logger.info(f"[ENTRY] {side} size={self.dynamic_lot} SL={sl_price} lvl={self.consec_losses}")

        await asyncio.sleep(2)
        await self._post_entry_flow(trigger_price, sl_offset)

    async def _post_entry_flow(self, trigger_price: float, sl_offset: float):
        status, filled = self._check_order_filled_retry(self.entry_order_id)
        if status != "closed":
            logger.error(f"Entry not filled: {status}")
            return

        tp_price = filled + sl_offset if self.open_position_side == "LONG" else filled - sl_offset
        tp_price = round_to_tick(tp_price, self.tick)
        close_side = 4 if self.open_position_side == "LONG" else 2
        tp_params = {
            "symbol": self.ws_symbol,
            "side": close_side,
            "openType": 2,
            "type": "2",
            "vol": str(self.dynamic_lot),
            "leverage": self.leverage,
            "price": f"{tp_price}",
            "priceProtect": "0",
        }
        tp_resp = self._place_order(tp_params)
        if tp_resp and tp_resp.get("success"):
            self.tp_order_ids[self.dynamic_lot] = tp_resp["data"]
            logger.info(f"[TP] placed @ {tp_price}")
        else:
            logger.error(f"TP order failed: {tp_resp}")

    # -------------------- API helpers --------------------
    def _place_order(self, param_json: dict):
        try:
            sign = generate_signature(self.uid, param_json)
            headers = {
                "Content-Type": "application/json",
                "Authorization": self.uid,
                "x-mxc-sign": sign["sign"],
                "x-mxc-nonce": sign["time"],
            }
            r = requests.post(self.ORDER_URL, headers=headers, json=param_json)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"_place_order error: {e}")
            return None

    def _check_order_filled(self, oid):
        try:
            od = self.exchange.fetch_order(str(oid), self.ccxt_symbol)
            return od["status"], od.get("average", 0.0)
        except Exception:
            return "unknown", None

    def _check_order_filled_retry(self, oid, max_retries=5, sleep_sec=2):
        for _ in range(max_retries):
            st, price = self._check_order_filled(oid)
            if st != "unknown":
                return st, price
            time.sleep(sleep_sec)
        return "unknown", None

    # -------------------- CANCEL helper --------------------
    def _cancel_orders(self, order_ids):
        if not order_ids:
            return
        try:
            sign_info = generate_signature(self.uid, order_ids)
            headers = {
                "Content-Type": "application/json",
                "Authorization": self.uid,
                "x-mxc-sign": sign_info["sign"],
                "x-mxc-nonce": sign_info["time"],
            }
            resp = requests.post(self.CANCEL_URL, headers=headers, json=order_ids)
            resp.raise_for_status()
            logger.info(f"[CANCEL] {order_ids} → {resp.json()}")
        except Exception as e:
            logger.error(f"Cancel error: {e}")

    # -------------------- Persistence --------------------
    def _save_trade_state(self):
        if not settings.PERSISTENCE_ENABLED:
            return
        state = {
            "trade_id": self.current_trade_id,
            "dynamic_lot_size": self.dynamic_lot,
            "consecutive_losses": self.consec_losses,
            "open_position_side": self.open_position_side,
            "last_trade_time": time.time(),
        }
        try:
            with open(settings.TRADE_STATE_FILE, "w") as f:
                json.dump(state, f)
        except Exception as e:
            logger.error(f"save state err: {e}")

    def _load_trade_state(self):
        try:
            if os.path.exists(settings.TRADE_STATE_FILE):
                with open(settings.TRADE_STATE_FILE) as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"load state err: {e}")
        return None

    def _restore_trade_state(self):
        st = self._load_trade_state()
        if not st:
            return
        self.current_trade_id = st.get("trade_id")
        self.dynamic_lot = st.get("dynamic_lot_size", self.base_lot)
        self.consec_losses = st.get("consecutive_losses", 0)
        self.open_position_side = st.get("open_position_side")
        self.last_trade_time = st.get("last_trade_time", time.time())
        logger.info("[RESTORE] trade state restored")

    # -------------------- Reset utils --------------------
    def reset_martingale(self):
        self.dynamic_lot = self.base_lot
        self.consec_losses = 0
        self.current_trade_id = None
        self.open_position_side = None
        self.entry_order_id = None
        self.tp_order_ids.clear()
        self.sl_order_ids.clear()
        self._save_trade_state()
        logger.info("[RESET] martingale reset")
