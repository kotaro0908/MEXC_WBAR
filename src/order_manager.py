import os
import json
import time
import math
import logging
import asyncio
from datetime import datetime
import ccxt
from curl_cffi import requests
from config.auth import generate_signature
from utils.logger import get_logger
from utils.log_utils import log_json, log_trade_result
from config.settings import settings

logger = get_logger(__name__)


class OrderManager:
    def __init__(self,
                 trade_logic,
                 ccxt_symbol,
                 ws_symbol,
                 lot_size,
                 leverage,
                 uid,
                 api_key,
                 api_secret,
                 position_threshold=0.95,
                 monitor=None):
        self.trade_logic = trade_logic
        self.trade_info = {}  # トレード情報保持用の辞書
        self.ccxt_symbol = ccxt_symbol
        self.ws_symbol = ws_symbol
        self.lot_size = lot_size
        self.leverage = leverage
        self.uid = uid
        self.api_key = api_key
        self.api_secret = api_secret
        self.position_threshold = position_threshold
        self.min_required_size = lot_size * position_threshold
        self.monitor = monitor

        self.ORDER_URL = "https://futures.mexc.com/api/v1/private/order/submit"
        self.CANCEL_URL = "https://futures.mexc.com/api/v1/private/order/cancel"

        self.open_position_side = None
        self.entry_order_id = None
        self.entry_price = None
        self.order_timestamp = None
        self.current_trade_id = None
        self.tp_order_ids = {}  # 利確注文のID管理
        self.sl_order_ids = {}  # ストップロス注文のID管理
        self.current_market_price = None
        self._filled_logged = False

        self.order_lock_until = 0
        self._last_order_status = None
        self._last_status_check_time = 0

        self.last_trade_time = time.time()

        self.dynamic_lot_size = self.lot_size
        self.martingale_factor = 2
        self.consecutive_losses = 0  # 追加: 連続損失カウンター

        self.exchange = ccxt.mexc({
            'apiKey': self.api_key,
            'secret': self.api_secret,
            'options': {
                'defaultType': 'future',
                'recvWindow': 60000
            },
            'enableRateLimit': True,
        })

        if settings.PERSISTENCE_ENABLED:
            self._restore_trade_state()

    def update_market_price(self, price: float):
        self.current_market_price = price

    def get_current_position_size(self) -> float:
        try:
            positions = self.exchange.fetch_positions([self.ccxt_symbol])
            total_size = 0.0
            for position in positions:
                contracts = float(position.get('contracts', 0))
                side = position.get('side', '').upper()
                if side == 'SHORT' and contracts < 0:
                    total_size = abs(contracts)
                elif side == 'LONG' and contracts > 0:
                    total_size = contracts
                if total_size > 0:
                    return total_size
            return 0.0
        except Exception as e:
            logger.error(f"Position size check failed: {e}")
            return 0.0

    def has_open_position_or_order(self) -> bool:
        if time.time() < self.order_lock_until:
            logger.debug(f"Order is locked until {datetime.fromtimestamp(self.order_lock_until)}")
            return True
        try:
            positions = self.exchange.fetch_positions([self.ccxt_symbol])
            for position in positions:
                size = float(position.get('contracts', 0))
                if abs(size) > 0.0001:
                    logger.debug(f"Found existing position with size: {size}")
                    return True
        except Exception as e:
            logger.error(f"Error checking positions: {e}")
            return True
        if self.tp_order_ids or self.sl_order_ids:
            logger.debug(f"Found existing TP/SL orders: TP={len(self.tp_order_ids)}, SL={len(self.sl_order_ids)}")
            return True
        if self.entry_order_id:
            status, _ = self._check_order_filled_retry(
                order_id=self.entry_order_id, max_retries=5, sleep_sec=3)
            if status == "open":
                logger.debug("Found open entry order")
                return True
        try:
            open_orders = self.exchange.fetch_open_orders(symbol=self.ccxt_symbol)
            if open_orders and len(open_orders) > 0:
                logger.debug(f"Found {len(open_orders)} open orders via API check")
                return True
        except Exception as e:
            logger.error(f"Error checking open orders: {e}")
            return True
        return False

    async def place_entry_order(self, side: str, trigger_price: float, trade_info: dict = None):
        if self.has_open_position_or_order():
            logger.info("Already have position or open order, skipping new entry")
            return

        # 方向転換時のロットサイズリセット処理 - 削除 (マーチンゲールを方向に関係なく適用)

        # 現在のマーチンゲール状態をログに出力
        logger.info(f"Placing {side} order with size {self.dynamic_lot_size} (consecutive losses: {self.consecutive_losses})")

        order_size = self.dynamic_lot_size
        if self.current_trade_id is None:
            # 永続化が有効なら復元済み状態を使用、なければ新規生成
            if settings.PERSISTENCE_ENABLED:
                saved_state = self._load_trade_state()
                if saved_state and not self._is_state_reset_needed(saved_state):
                    self.current_trade_id = saved_state.get('trade_id')
                    self.dynamic_lot_size = saved_state.get('dynamic_lot_size', self.lot_size)
                    self.consecutive_losses = saved_state.get('consecutive_losses', 0)  # 追加
                    self.open_position_side = saved_state.get('open_position_side')
                    logger.info(f"Restored trade state: ID={self.current_trade_id}, lot={self.dynamic_lot_size}, consecutive_losses={self.consecutive_losses}")
                else:
                    self.current_trade_id = f"T{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            else:
                self.current_trade_id = f"T{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        self.open_position_side = side
        self.trade_info = trade_info or {}
        self.last_trade_time = time.time()

        price_precision = 1
        if side == "SHORT":
            order_side = 3
            sl_price = round(trigger_price + self.trade_logic.offset_sl, price_precision)
            tp_price = round(trigger_price - self.trade_logic.offset_tp, price_precision)
        else:
            order_side = 1
            sl_price = round(trigger_price - self.trade_logic.offset_sl, price_precision)
            tp_price = round(trigger_price + self.trade_logic.offset_tp, price_precision)

        self.trade_info["stop_loss_price"] = sl_price
        self.trade_info["take_profit_price"] = tp_price
        # 追加: 現在のロットサイズと次回予測ロットサイズを記録
        self.trade_info["current_lot_size"] = self.dynamic_lot_size
        self.trade_info["next_lot_size"] = math.ceil(self.dynamic_lot_size * self.martingale_factor) if self.consecutive_losses > 0 else self.lot_size
        self.trade_info["consecutive_losses"] = self.consecutive_losses
        self.trade_info["martingale_factor"] = self.martingale_factor

        # ※TP注文は除外し、SLのみエントリー注文に付与
        params = {
            "symbol": self.ws_symbol,
            "side": order_side,
            "openType": 2,
            "type": "5",  # 成行注文
            "vol": str(round(self.dynamic_lot_size, 3)),
            "leverage": self.leverage,
            "priceProtect": "0",
            "stopLossPrice": f"{sl_price}"
            # takeProfitPrice はここでは指定しない
        }

        logger.debug(f"Placing entry order: Size {round(self.dynamic_lot_size, 3)}, SL {sl_price}")
        response = self._place_order(params)
        if response and response.get("success"):
            self.entry_order_id = response["data"]
            self.order_timestamp = datetime.utcnow().isoformat()
            self.order_lock_until = time.time() + 5
            logger.info("=" * 40)
            logger.info(f"[ORDER PLACED] {side} Entry - Size: {round(self.dynamic_lot_size, 3)}, SL: {sl_price}, Consecutive Losses: {self.consecutive_losses}")
            logger.info("=" * 40)
            logger.info("Waiting 5 seconds for entry order confirmation...")
            self._save_trade_state()
            await asyncio.sleep(3)
            status, filled_price = self._check_order_filled_retry(self.entry_order_id, max_retries=3, sleep_sec=2)
            if status != "closed":
                logger.error(f"Entry order not confirmed. Status: {status}")
                return
            logger.info("Entry order confirmed. Now placing TP order separately...")
            # TP注文はエントリー確定後に別途発注する
            tp_success = await self.place_take_profit_order(self.dynamic_lot_size, filled_price)
            if tp_success:
                logger.info("TP order placed successfully.")
            else:
                logger.warning("TP order placement failed.")
            self._save_trade_state()
            await asyncio.sleep(2)
        else:
            logger.error(f"Failed to place entry order: {response}")

    async def place_stop_loss_order(self, position_size: float, entry_price: float):
        # ※既にエントリー注文に SL が付いている場合は、個別発注不要
        logger.debug("place_stop_loss_order: Not used because SL is attached to entry order.")
        return True

    async def place_take_profit_order(self, position_size: float, filled_price: float):
        if position_size in self.tp_order_ids:
            logger.debug(f"TP Skip - Order exists for size: {position_size}")
            return True

        if position_size <= 0:
            logger.debug(f"TP Skip - Invalid size: {position_size}")
            return False

        price_precision = 1
        if self.open_position_side == "SHORT":
            tp_price = round(filled_price - self.trade_logic.offset_tp, price_precision)
            close_side = 2
        else:
            tp_price = round(filled_price + self.trade_logic.offset_tp, price_precision)
            close_side = 4

        logger.debug(f"TP Order Details - Side: {self.open_position_side}, Close Side: {close_side}, Price: {tp_price}")

        tp_params = {
            "symbol": self.ws_symbol,
            "side": close_side,
            "openType": 2,
            "type": "2",  # ポストオンリー注文
            "vol": str(position_size),
            "leverage": self.leverage,
            "price": f"{tp_price}",
            "priceProtect": "0"
        }

        logger.debug(f"TP Order Parameters: {tp_params}")

        max_attempts = 3
        attempt = 0
        tp_success = False
        while attempt < max_attempts and not tp_success:
            tp_response = self._place_order(tp_params)
            if tp_response and tp_response.get("success"):
                self.tp_order_ids[position_size] = tp_response["data"]
                logger.info(f"[TP ORDER PLACED] Size: {position_size}, Price: {tp_price}")
                tp_success = True
            else:
                logger.warning(f"TP order failed on attempt {attempt+1}: {tp_response}. Retrying after delay.")
                await asyncio.sleep(1)
                attempt += 1
        return tp_success

    async def check_orders_status(self):
        if self.entry_order_id:
            status, filled_price = self._check_order_filled_retry(
                order_id=self.entry_order_id, max_retries=5, sleep_sec=3)
            position_size = self.dynamic_lot_size
            logger.debug(f"Entry order status check - status: {status}, position size: {position_size}")
            if status == "closed":
                self.entry_order_id = None
                logger.info("=" * 40)
                logger.info(
                    f"[ORDER FILLED] {self.open_position_side} at Price: {filled_price}, Position: {position_size}")
                logger.info("=" * 40)
                self.trade_info["entry_price"] = filled_price
                self.trade_info["trade_id"] = self.current_trade_id
                self.trade_info["current_lot_size"] = self.dynamic_lot_size
                self.trade_info["next_lot_size"] = math.ceil(
                    self.dynamic_lot_size * self.martingale_factor) if self.consecutive_losses > 0 else self.lot_size
                log_json("ENTRY_FILLED", {
                    "trade_id": self.current_trade_id,
                    "side": self.open_position_side,
                    "filled_price": filled_price,
                    "position_size": position_size,
                    "order_timestamp": self.order_timestamp,
                    "consecutive_losses": self.consecutive_losses,
                    "current_lot_size": self.dynamic_lot_size,
                    "next_lot_size": math.ceil(
                        self.dynamic_lot_size * self.martingale_factor) if self.consecutive_losses > 0 else self.lot_size
                })
                self.entry_price = filled_price or 0
                self._save_trade_state()
                return
            elif status == "canceled":
                logger.info("=" * 40)
                logger.info("[ORDER CANCELED]")
                logger.info("=" * 40)
                self._clear_order_info()
                return
            elif self._is_entry_order_expired():
                logger.debug("Entry order expired, cancelling order")
                self._cancel_orders([self.entry_order_id])
                await asyncio.sleep(1)
                cancel_status, _ = self._check_order_filled_retry(
                    order_id=self.entry_order_id, max_retries=5, sleep_sec=3)
                if cancel_status == "canceled":
                    logger.debug("Order cancel confirmed, clearing memory")
                    self._clear_order_info()
                else:
                    logger.error(f"Order cancel failed, status: {cancel_status}")
                return

        for size, tp_order_id in list(self.tp_order_ids.items()):
            status, filled_price = self._check_order_filled_retry(tp_order_id)
            logger.debug(f"TP order check - order_id: {tp_order_id}, status: {status}")
            if status == "canceled":
                logger.info(f"TP order {tp_order_id} was canceled, removing from tracking")
                try:
                    # entry_priceキーが存在するか確認し、なければエントリー価格を取得
                    entry_price = self.trade_info.get("entry_price", self.entry_price)
                    if entry_price is None:
                        logger.error(
                            f"Missing entry_price in trade_info during TP canceled processing: {self.trade_info}")
                        entry_price = 0  # デフォルト値を設定

                    if filled_price is not None:
                        pnl = (filled_price - entry_price) if self.open_position_side == "LONG" else \
                            (entry_price - filled_price)
                    else:
                        pnl = None

                    # 欠けている可能性のあるtradeキーのデフォルト値を設定
                    trade_info_copy = self.trade_info.copy() if hasattr(self, 'trade_info') and self.trade_info else {}
                    if "entry_price" not in trade_info_copy and self.entry_price is not None:
                        trade_info_copy["entry_price"] = self.entry_price
                    if "direction" not in trade_info_copy and self.open_position_side:
                        trade_info_copy["direction"] = self.open_position_side

                    trade_result = {**trade_info_copy,
                                    "exit_type": "SL",
                                    "exit_price": filled_price,
                                    "pnl": pnl,
                                    "current_lot_size": self.dynamic_lot_size,
                                    "next_lot_size": math.ceil(self.dynamic_lot_size * self.martingale_factor),
                                    "trade_id": self.current_trade_id}
                except Exception as e:
                    logger.error(f"Error processing canceled TP order: {e}")
                    trade_result = {
                        "exit_type": "SL",
                        "exit_price": filled_price,
                        "current_lot_size": self.dynamic_lot_size,
                        "next_lot_size": math.ceil(self.dynamic_lot_size * self.martingale_factor),
                        "trade_id": self.current_trade_id,
                        "direction": self.open_position_side
                    }

                log_trade_result(trade_result)
                if self.monitor:
                    self.monitor.update_trade_result(trade_result)
                del self.tp_order_ids[size]
                if size in self.sl_order_ids:
                    sl_order_id = self.sl_order_ids[size]
                    self._cancel_orders([sl_order_id])
                    del self.sl_order_ids[size]
                # 損失が出たのでマーチンゲールを適用
                self.consecutive_losses += 1
                self.dynamic_lot_size = math.ceil(self.dynamic_lot_size * self.martingale_factor)
                logger.info(
                    f"Martingale applied. Consecutive losses: {self.consecutive_losses}, New lot size: {self.dynamic_lot_size}")
                self._save_trade_state()
                continue

            if status == "closed":
                logger.info("=" * 40)
                logger.info(f"[TP ORDER FILLED] Size: {size}, Filled Price: {filled_price}")
                logger.info("=" * 40)
                try:
                    # entry_priceキーが存在するか確認し、なければエントリー価格を取得
                    entry_price = self.trade_info.get("entry_price", self.entry_price)
                    if entry_price is None:
                        logger.error(f"Missing entry_price in trade_info during TP fill processing: {self.trade_info}")
                        entry_price = 0  # デフォルト値を設定

                    pnl = (filled_price - entry_price) if self.open_position_side == "LONG" else \
                        (entry_price - filled_price)

                    # 欠けている可能性のあるtradeキーのデフォルト値を設定
                    trade_info_copy = self.trade_info.copy() if hasattr(self, 'trade_info') and self.trade_info else {}
                    if "entry_price" not in trade_info_copy and self.entry_price is not None:
                        trade_info_copy["entry_price"] = self.entry_price
                    if "direction" not in trade_info_copy and self.open_position_side:
                        trade_info_copy["direction"] = self.open_position_side

                    trade_result = {**trade_info_copy,
                                    "exit_type": "TP",
                                    "exit_price": filled_price,
                                    "pnl": pnl,
                                    "current_lot_size": self.dynamic_lot_size,
                                    "next_lot_size": self.lot_size,
                                    "trade_id": self.current_trade_id}
                except Exception as e:
                    logger.error(f"Error processing TP order fill: {e}")
                    trade_result = {
                        "exit_type": "TP",
                        "exit_price": filled_price,
                        "current_lot_size": self.dynamic_lot_size,
                        "next_lot_size": self.lot_size,
                        "trade_id": self.current_trade_id,
                        "direction": self.open_position_side
                    }

                log_trade_result(trade_result)
                if self.monitor:
                    self.monitor.update_trade_result(trade_result)
                log_json("TP_ORDER_FILLED", {
                    "trade_id": self.current_trade_id,
                    "position_size": size,
                    "filled_price": filled_price,
                    "consecutive_losses": self.consecutive_losses,
                    "current_lot_size": self.dynamic_lot_size,
                    "next_lot_size": self.lot_size
                })
                del self.tp_order_ids[size]
                if size in self.sl_order_ids:
                    sl_order_id = self.sl_order_ids[size]
                    self._cancel_orders([sl_order_id])
                    del self.sl_order_ids[size]
                # 利益が出たのでマーチンゲールをリセット
                self.consecutive_losses = 0
                self.dynamic_lot_size = self.lot_size
                logger.info(
                    f"Trade won. Resetting martingale: consecutive_losses={self.consecutive_losses}, lot_size={self.dynamic_lot_size}")
                self._save_trade_state()
                self._clear_order_info()
                # self.current_trade_id = None  # 修正: TP決済後も取引IDを維持
                return

        for size, sl_order_id in list(self.sl_order_ids.items()):
            status, filled_price = self._check_order_filled_retry(sl_order_id)
            logger.debug(f"SL order check - order_id: {sl_order_id}, status: {status}")
            if status == "closed":
                logger.info("=" * 40)
                logger.info(f"[SL ORDER FILLED] Size: {size}, Filled Price: {filled_price}")
                logger.info("=" * 40)
                try:
                    # entry_priceキーが存在するか確認し、なければエントリー価格を取得
                    entry_price = self.trade_info.get("entry_price", self.entry_price)
                    if entry_price is None:
                        logger.error(f"Missing entry_price in trade_info during SL fill processing: {self.trade_info}")
                        entry_price = 0  # デフォルト値を設定

                    pnl = (filled_price - entry_price) if self.open_position_side == "LONG" else \
                        (entry_price - filled_price)

                    # 欠けている可能性のあるtradeキーのデフォルト値を設定
                    trade_info_copy = self.trade_info.copy() if hasattr(self, 'trade_info') and self.trade_info else {}
                    if "entry_price" not in trade_info_copy and self.entry_price is not None:
                        trade_info_copy["entry_price"] = self.entry_price
                    if "direction" not in trade_info_copy and self.open_position_side:
                        trade_info_copy["direction"] = self.open_position_side

                    trade_result = {**trade_info_copy,
                                    "exit_type": "SL",
                                    "exit_price": filled_price,
                                    "pnl": pnl,
                                    "current_lot_size": self.dynamic_lot_size,
                                    "next_lot_size": math.ceil(self.dynamic_lot_size * self.martingale_factor),
                                    "trade_id": self.current_trade_id}
                except Exception as e:
                    logger.error(f"Error processing SL order fill: {e}")
                    trade_result = {
                        "exit_type": "SL",
                        "exit_price": filled_price,
                        "current_lot_size": self.dynamic_lot_size,
                        "next_lot_size": math.ceil(self.dynamic_lot_size * self.martingale_factor),
                        "trade_id": self.current_trade_id,
                        "direction": self.open_position_side
                    }

                log_trade_result(trade_result)
                if self.monitor:
                    self.monitor.update_trade_result(trade_result)
                log_json("SL_ORDER_FILLED", {
                    "trade_id": self.current_trade_id,
                    "position_size": size,
                    "filled_price": filled_price,
                    "consecutive_losses": self.consecutive_losses,
                    "current_lot_size": self.dynamic_lot_size,
                    "next_lot_size": math.ceil(self.dynamic_lot_size * self.martingale_factor)
                })
                del self.sl_order_ids[size]
                if size in self.tp_order_ids:
                    tp_order_id = self.tp_order_ids[size]
                    self._cancel_orders([tp_order_id])
                    del self.tp_order_ids[size]
                # 損失が出たのでマーチンゲールを適用
                self.consecutive_losses += 1
                self.dynamic_lot_size = math.ceil(self.dynamic_lot_size * self.martingale_factor)
                logger.info(
                    f"Martingale applied. Consecutive losses: {self.consecutive_losses}, New lot size: {self.dynamic_lot_size}")
                self._save_trade_state()
                self._clear_order_info()
                # self.current_trade_id = None  # 修正: SL決済後も取引IDを維持
                return

    def _check_order_filled_retry(self, order_id, max_retries=5, sleep_sec=3):
        for attempt in range(1, max_retries + 1):
            status, filled_price = self._check_order_filled(order_id)
            if status not in ("unknown", ""):
                return (status, filled_price)
            logger.debug(f"fetch_order attempt {attempt}/{max_retries} for order_id {order_id}")
            time.sleep(sleep_sec)
        return ("unknown", None)

    def _cancel_orders(self, order_ids):
        if not order_ids:
            return
        try:
            sign_info = generate_signature(self.uid, order_ids)
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
                "Authorization": self.uid,
                "x-mxc-sign": sign_info["sign"],
                "x-mxc-nonce": sign_info["time"]
            }
            resp = requests.post(self.CANCEL_URL, headers=headers, json=order_ids)
            resp.raise_for_status()
            data = resp.json()
            logger.info("=" * 40)
            logger.info(f"[ORDER CANCEL RESPONSE] {data}")
            logger.info("=" * 40)
            log_json("ORDER_CANCELED", {
                "trade_id": self.current_trade_id,
                "order_ids": order_ids,
                "cancel_response": data
            })
        except Exception as e:
            logger.error(f"_cancel_orders failed: {e}")

    def _clear_order_info(self):
        self.entry_order_id = None
        self.order_timestamp = None
        self.order_lock_until = time.time() + 5
        self.tp_order_ids = {}
        self.sl_order_ids = {}
        self._clear_local_cache()
        self._filled_logged = False
        # self.trade_info = {}  # この行があれば削除または修正
        # self.open_position_side = None  # 修正: ポジション方向を維持

    def _clear_local_cache(self):
        self._last_order_status = None
        self._last_status_check_time = 0

    def _is_entry_order_expired(self) -> bool:
        if not self.order_timestamp:
            return False
        t0 = datetime.fromisoformat(self.order_timestamp)
        now_ = datetime.utcnow()
        elapsed_sec = (now_ - t0).total_seconds()
        if elapsed_sec <= self.trade_logic.order_timeout_sec:
            return False
        try:
            try:
                od = self.exchange.fetch_order(str(self.entry_order_id), self.ccxt_symbol)
                if od and od.get('filled', 0) > 0 and od.get('filled', 0) < od.get('amount', 0):
                    logger.info(f"Partial fill detected: {od.get('filled')}/{od.get('amount')}. Extending timeout.")
                    return False
            except Exception as e:
                logger.error(f"Partial fill check error: {e}")
            if not self.current_market_price:
                logger.warning("No market price available for timeout extension check")
                return True
            order = self.exchange.fetch_order(str(self.entry_order_id), self.ccxt_symbol)
            if not order:
                return True
            entry_price = float(order.get('price', 0))
            if self.open_position_side == "SHORT":
                entry_base_price = entry_price - self.trade_logic.offset_entry - 0.00001
                logger.debug(f"SHORT order timeout check: current={self.current_market_price:.6f}, base={entry_base_price:.6f}")
                return not (self.current_market_price >= entry_base_price)
            elif self.open_position_side == "LONG":
                entry_base_price = entry_price + self.trade_logic.offset_entry + 0.00001
                logger.debug(f"LONG order timeout check: current={self.current_market_price:.6f}, base={entry_base_price:.6f}")
                return not (self.current_market_price <= entry_base_price)
            else:
                logger.error("Order type is undefined. Cancelling order.")
                return True
        except Exception as e:
            logger.error(f"Order timeout extension check failed: {e}")
            return True

    def _place_order(self, param_json):
        if not self.uid:
            logger.error("UID is not set.")
            return None
        try:
            sign_info = generate_signature(self.uid, param_json)
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
                "Authorization": self.uid,
                "x-mxc-sign": sign_info["sign"],
                "x-mxc-nonce": sign_info["time"]
            }
            resp = requests.post(self.ORDER_URL, headers=headers, json=param_json)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"_place_order failed: {e}")
            return None

    def _check_order_filled(self, order_id) -> tuple:
        if not order_id:
            return ("unknown", None)
        try:
            od = self.exchange.fetch_order(str(order_id), self.ccxt_symbol)
            return (od.get("status", ""), od.get("average", 0.0))
        except Exception:
            return ("unknown", None)

    def _save_trade_state(self):
        if not settings.PERSISTENCE_ENABLED:
            return
        try:
            state = {
                'trade_id': self.current_trade_id,
                'dynamic_lot_size': self.dynamic_lot_size,
                'consecutive_losses': self.consecutive_losses,  # 追加: 連続損失カウンター
                'open_position_side': self.open_position_side,
                'last_trade_time': time.time(),
                'tp_order_ids': self.tp_order_ids,
                'sl_order_ids': self.sl_order_ids,
                'entry_price': self.entry_price
            }
            with open(settings.TRADE_STATE_FILE, 'w') as f:
                json.dump(state, f)
            logger.debug(f"Saved trade state: ID={self.current_trade_id}, lot={self.dynamic_lot_size}, consecutive_losses={self.consecutive_losses}")
        except Exception as e:
            logger.error(f"Failed to save trade state: {e}")

    def _load_trade_state(self):
        if not settings.PERSISTENCE_ENABLED:
            return None
        try:
            if os.path.exists(settings.TRADE_STATE_FILE):
                with open(settings.TRADE_STATE_FILE, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load trade state: {e}")
        return None

    def _restore_trade_state(self):
        if not settings.PERSISTENCE_ENABLED:
            return
        saved_state = self._load_trade_state()
        if not saved_state:
            return
        if self._is_state_reset_needed(saved_state):
            logger.info("Martingale state reset due to timeout or missing data")
            self.reset_martingale()
            return
        self.current_trade_id = saved_state.get('trade_id')
        self.dynamic_lot_size = saved_state.get('dynamic_lot_size', self.lot_size)
        self.consecutive_losses = saved_state.get('consecutive_losses', 0)  # 追加: 連続損失カウンター
        self.open_position_side = saved_state.get('open_position_side')
        self.tp_order_ids = saved_state.get('tp_order_ids', {})
        self.sl_order_ids = saved_state.get('sl_order_ids', {})
        self.entry_price = saved_state.get('entry_price')
        self.last_trade_time = saved_state.get('last_trade_time', time.time())
        logger.info(f"Restored trade state: ID={self.current_trade_id}, lot={self.dynamic_lot_size}, consecutive_losses={self.consecutive_losses}")

    def _is_state_reset_needed(self, saved_state):
        if not saved_state:
            return True
        if 'trade_id' not in saved_state or 'dynamic_lot_size' not in saved_state:
            return True
        if settings.MARTINGALE_RESET_TIMEOUT > 0:
            last_trade_time = saved_state.get('last_trade_time', 0)
            elapsed_sec = time.time() - last_trade_time
            if elapsed_sec > settings.MARTINGALE_RESET_TIMEOUT:
                logger.info(f"Martingale state reset: {elapsed_sec:.0f}s elapsed (timeout: {settings.MARTINGALE_RESET_TIMEOUT}s)")
                return True
        return False

    def reset_martingale(self):
        logger.info("Resetting martingale counter")
        self.dynamic_lot_size = self.lot_size
        self.consecutive_losses = 0  # 追加: 連続損失カウンターもリセット
        self.current_trade_id = None
        self.entry_order_id = None
        self.entry_price = None
        self.order_timestamp = None
        self.tp_order_ids = {}
        self.sl_order_ids = {}
        self.open_position_side = None
        if settings.PERSISTENCE_ENABLED:
            try:
                state = {
                    'trade_id': None,
                    'dynamic_lot_size': self.lot_size,
                    'consecutive_losses': 0,  # 追加: 連続損失カウンター
                    'last_trade_time': time.time(),
                    'reset_time': time.time()
                }
                with open(settings.TRADE_STATE_FILE, 'w') as f:
                    json.dump(state, f)
                logger.debug("Martingale reset state saved")
            except Exception as e:
                logger.error(f"Failed to save reset state: {e}")