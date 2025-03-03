import os
import time
import json
import asyncio
from datetime import datetime
import math
import ccxt
from curl_cffi import requests
from config.auth import generate_signature
from utils.logger import get_logger
from utils.log_utils import log_json, log_trade_result
from config.settings import settings

logger = get_logger(__name__)


class OrderManager:
    def __init__(
            self,
            trade_logic,
            ccxt_symbol,
            ws_symbol,
            lot_size,
            leverage,
            uid,
            api_key,
            api_secret,
            position_threshold=0.95,
            monitor=None  # 追加: 監視モジュールの参照
    ):
        self.trade_logic = trade_logic
        self.trade_info = {}  # トレード情報保持用の辞書を追加
        self.ccxt_symbol = ccxt_symbol
        self.ws_symbol = ws_symbol
        self.lot_size = lot_size
        self.leverage = leverage
        self.uid = uid
        self.api_key = api_key
        self.api_secret = api_secret
        self.position_threshold = position_threshold
        self.min_required_size = lot_size * position_threshold
        self.monitor = monitor  # 追加: 監視モジュールの参照を保持

        self.ORDER_URL = "https://futures.mexc.com/api/v1/private/order/submit"
        self.CANCEL_URL = "https://futures.mexc.com/api/v1/private/order/cancel"

        self.open_position_side = None
        self.entry_order_id = None
        self.entry_price = None
        self.order_timestamp = None
        self.current_trade_id = None
        self.tp_order_ids = {}  # 利確注文のID管理
        self.current_market_price = None
        self._filled_logged = False  # 追加

        self.order_lock_until = 0
        self._last_order_status = None
        self._last_status_check_time = 0

        # 最終トレード時間の追加（永続化用）
        self.last_trade_time = time.time()

        self.exchange = ccxt.mexc({
            'apiKey': self.api_key,
            'secret': self.api_secret,
            'options': {
                'defaultType': 'future',
                'recvWindow': 60000
            },
            'enableRateLimit': True,
        })

        # マーチンゲール手法用の初期設定
        # リスクリワード比率は、trade_logic の offset_tp (TP_AMOUNT) と offset_sl (SL_AMOUNT) により決定
        # マーチンゲール手法用の初期設定（リスクリワードに関係なく常に2倍）
        self.martingale_factor = 2
        self.dynamic_lot_size = self.lot_size

        # 状態復元処理を初期化時に実行（永続化が有効な場合のみ）
        if settings.PERSISTENCE_ENABLED:
            self._restore_trade_state()

    def update_market_price(self, price: float):
        """現在の市場価格を更新"""
        self.current_market_price = price

    def get_current_position_size(self) -> float:
        try:
            positions = self.exchange.fetch_positions([self.ccxt_symbol])
            total_size = 0.0
            for position in positions:
                contracts = float(position['contracts'])
                side = position.get('side', '').upper()
                # サイドとコントラクトの値に基づいてポジションサイズを計算
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
        # ロック期間中はTrueを返す
        if time.time() < self.order_lock_until:
            logger.debug(f"Order is locked until {datetime.fromtimestamp(self.order_lock_until)}")
            return True

        # 実際のポジションチェック
        if self.get_current_position_size() > 0:
            logger.debug("Found existing position")
            return True

        # オープンオーダーのチェック
        if self.entry_order_id:
            status, _ = self._check_order_filled_retry(
                order_id=self.entry_order_id,
                max_retries=5,
                sleep_sec=3
            )
            # openのみTrue、closed/canceledはFalse
            if status == "open":
                logger.debug("Found open entry order")
                return True

        # TPオーダーのチェック
        for tp_order_id in self.tp_order_ids.values():
            status, _ = self._check_order_filled_retry(tp_order_id)
            if status == "open":
                logger.debug("Found open TP order")
                return True

        return False

    async def place_entry_order(self, side: str, trigger_price: float, trade_info: dict = None):
        if self.has_open_position_or_order():
            logger.info("Already have position or open order, skipping new entry")
            return

        order_size = self.dynamic_lot_size

        # トレードID生成（永続化機能による改良）
        if self.current_trade_id is None:
            # 永続化が有効であれば、保存された状態から復元
            if settings.PERSISTENCE_ENABLED:
                saved_state = self._load_trade_state()
                if saved_state and not self._is_state_reset_needed(saved_state):
                    self.current_trade_id = saved_state.get('trade_id')
                    self.dynamic_lot_size = saved_state.get('dynamic_lot_size', self.lot_size)
                    self.open_position_side = saved_state.get('open_position_side')
                    logger.info(f"Restored trade state: ID={self.current_trade_id}, lot={self.dynamic_lot_size}")
                else:
                    # 新規トレードID生成
                    self.current_trade_id = f"T{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            else:
                # 永続化無効の場合は通常通り新規ID生成
                self.current_trade_id = f"T{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        self.open_position_side = side
        self.trade_info = trade_info or {}
        # 最終トレード時間を更新
        self.last_trade_time = time.time()

        if side == "SHORT":
            order_side = 3
            order_price = trigger_price + self.trade_logic.offset_entry
            sl_price = order_price + self.trade_logic.offset_sl
        else:
            order_side = 1
            order_price = trigger_price - self.trade_logic.offset_entry
            sl_price = order_price - self.trade_logic.offset_sl

        # stop loss価格を trade_info に保存
        self.trade_info["stop_loss_price"] = sl_price

        params = {
            "symbol": self.ws_symbol,
            "side": order_side,
            "openType": 2,
            "type": "2",
            "vol": str(self.dynamic_lot_size),
            "leverage": self.leverage,
            "price": f"{order_price:.6f}",
            "priceProtect": "0",
            "stopLossPrice": f"{sl_price:.6f}"
        }

        logger.debug(f"Placing entry order with size: {order_size}")
        response = self._place_order(params)
        if response and response.get("success"):
            self.entry_order_id = response["data"]
            self.order_timestamp = datetime.utcnow().isoformat()
            self.order_lock_until = time.time() + 5
            logger.info("=" * 40)
            logger.info(
                f"[ORDER PLACED] {side} Entry - Size: {order_size}, Price: {order_price:.6f}, Stop Loss: {sl_price:.6f}")
            logger.info("=" * 40)
            logger.info("Waiting 5 seconds for MEXC to register the new order...")
            # トレード状態を保存
            self._save_trade_state()
            await asyncio.sleep(2)
        else:
            logger.error(f"Failed to place entry order: {response}")

    async def check_orders_status(self):
        if self.entry_order_id:
            status, filled_price = self._check_order_filled_retry(
                order_id=self.entry_order_id,
                max_retries=5,
                sleep_sec=3
            )
            position_size = self.dynamic_lot_size
            logger.debug(f"Order status check - status: {status}, position size: {position_size}")
            if status == "closed":
                # 注文IDをクリアして重複検知を防ぐ
                self.entry_order_id = None

                # 損切り判定：APIから取得した filled_price とエントリー時に設定した stop_loss_price を比較
                logger.debug(
                    f"SL check - side: {self.open_position_side}, filled_price: {filled_price}, stop_loss_price: {self.trade_info.get('stop_loss_price')}")
                is_stop_loss = False
                if self.open_position_side == "LONG":
                    # LONGの場合：filled_price が stop_loss_price 以下なら損切り
                    if filled_price <= self.trade_info.get("stop_loss_price", 0):
                        is_stop_loss = True
                        logger.debug(
                            f"LONG SL triggered - filled_price({filled_price}) <= stop_loss({self.trade_info.get('stop_loss_price', 0)})")
                elif self.open_position_side == "SHORT":
                    # SHORTの場合：filled_price が stop_loss_price 以上なら損切り
                    if filled_price >= self.trade_info.get("stop_loss_price", 0):
                        is_stop_loss = True
                        logger.debug(
                            f"SHORT SL triggered - filled_price({filled_price}) >= stop_loss({self.trade_info.get('stop_loss_price', 0)})")

                if is_stop_loss:
                    logger.info("Stop loss triggered. Updating dynamic lot size (loss).")

                    # トレード結果の作成
                    trade_result = {
                        **self.trade_info,
                        "exit_type": "SL",
                        "exit_price": filled_price,
                        "pnl": None
                    }

                    # トレード結果をログに記録
                    log_trade_result(trade_result)

                    # 監視モジュールにトレード結果を通知 (追加)
                    if self.monitor:
                        self.monitor.update_trade_result(trade_result)

                    self.dynamic_lot_size = math.ceil(self.dynamic_lot_size * self.martingale_factor)
                    # 状態を保存（ロットサイズを増加させた後）
                    self._save_trade_state()
                    self._clear_order_info()
                    return

                # もし損切りでなければ、通常の勝ち処理としてTP注文を発注
                logger.info("=" * 40)
                logger.info(
                    f"[ORDER FILLED] {self.open_position_side} at Price: {filled_price:.6f}, Current Position: {position_size}")
                logger.info("=" * 40)

                self.trade_info["entry_price"] = filled_price
                self.trade_info["trade_id"] = self.current_trade_id

                log_json("ENTRY_FILLED", {
                    "trade_id": self.current_trade_id,
                    "side": self.open_position_side,
                    "filled_price": filled_price,
                    "position_size": position_size,
                    "order_timestamp": self.order_timestamp
                })

                self.entry_price = filled_price or 0
                logger.debug(
                    f"Preparing TP order - Side: {self.open_position_side}, Entry Price: {self.entry_price}, Position: {position_size}")
                logger.info("Waiting 2 seconds before placing TP order to avoid API limit issues...")
                # 状態を保存
                self._save_trade_state()
                await asyncio.sleep(2)

                if position_size > 0:
                    logger.debug("Attempting to place TP order...")
                    success = await self.place_take_profit_order(position_size, filled_price)
                    if success:
                        logger.debug(f"TP order placement result: {success}")
                    else:
                        logger.error("Failed to place TP order, keeping position info")
                        self._clear_order_info()
                return
            elif status == "canceled":
                logger.info("=" * 40)
                logger.info("[ORDER CANCELED]")
                logger.info("=" * 40)
                self._clear_order_info()
                return
            elif self._is_entry_order_expired():
                logger.debug("Order expired, cancelling order")
                self._cancel_orders([self.entry_order_id])
                await asyncio.sleep(1)
                cancel_status, _ = self._check_order_filled_retry(
                    order_id=self.entry_order_id,
                    max_retries=5,
                    sleep_sec=3
                )
                if cancel_status == "canceled":
                    logger.debug("Order cancel confirmed, clearing memory")
                    self._clear_order_info()
                else:
                    logger.error(f"Order cancel failed, status: {cancel_status}")
                return

        # TPオーダーのチェック
        for size, tp_order_id in list(self.tp_order_ids.items()):
            status, filled_price = self._check_order_filled_retry(tp_order_id)
            logger.debug(f"TP order check - order_id: {tp_order_id}, status: {status}")
            if status == "canceled":
                logger.info(f"TP order {tp_order_id} was canceled, removing from tracking")
                if filled_price is not None:
                    pnl = filled_price - self.trade_info["entry_price"] if self.open_position_side == "LONG" else \
                        self.trade_info["entry_price"] - filled_price
                else:
                    pnl = None

                # トレード結果の作成
                trade_result = {
                    **self.trade_info,
                    "exit_type": "SL",
                    "exit_price": filled_price,
                    "pnl": pnl
                }

                # トレード結果をログに記録
                log_trade_result(trade_result)

                # 監視モジュールにトレード結果を通知 (追加)
                if self.monitor:
                    self.monitor.update_trade_result(trade_result)

                del self.tp_order_ids[size]
                self.dynamic_lot_size = math.ceil(self.dynamic_lot_size * self.martingale_factor)
                # 状態を保存（ロットサイズ変更後）
                self._save_trade_state()
                continue
            if status == "closed":
                logger.info("=" * 40)
                logger.info(f"[TP ORDER FILLED] Size: {size}, Filled Price: {filled_price}")
                logger.info("=" * 40)
                pnl = filled_price - self.trade_info["entry_price"] if self.open_position_side == "LONG" else \
                    self.trade_info["entry_price"] - filled_price

                # トレード結果の作成
                trade_result = {
                    **self.trade_info,
                    "exit_type": "TP",
                    "exit_price": filled_price,
                    "pnl": pnl
                }

                # トレード結果をログに記録
                log_trade_result(trade_result)

                # 監視モジュールにトレード結果を通知 (追加)
                if self.monitor:
                    self.monitor.update_trade_result(trade_result)

                log_json("TP_ORDER_FILLED", {
                    "trade_id": self.current_trade_id,
                    "position_size": size,
                    "filled_price": filled_price
                })

                del self.tp_order_ids[size]
                self.dynamic_lot_size = self.lot_size  # 勝ちの場合は基本ロットサイズにリセット
                # 状態を保存（ロットサイズリセット後）
                self._save_trade_state()
                self._clear_order_info()
                logger.debug("Position and order info cleared after TP filled")
                return

    async def place_take_profit_order(self, position_size: float, filled_price: float):
        if position_size in self.tp_order_ids:
            logger.debug(f"TP Skip - Order exists for size: {position_size}")
            return True

        if position_size <= 0:
            logger.debug(f"TP Skip - Invalid size: {position_size}")
            return False

        if self.open_position_side == "SHORT":
            tp_price = filled_price - self.trade_logic.offset_tp
            close_side = 2  # SHORTポジションのクローズ
        else:
            tp_price = filled_price + self.trade_logic.offset_tp
            close_side = 4  # LONGポジションのクローズ

        logger.debug(
            f"TP Order Details - Side: {self.open_position_side}, Close Side: {close_side}, Price: {tp_price:.6f}")

        tp_params = {
            "symbol": self.ws_symbol,
            "side": close_side,
            "openType": 2,
            "type": "1",
            "vol": str(position_size),
            "leverage": self.leverage,
            "price": f"{tp_price:.6f}",
            "priceProtect": "0"
        }

        logger.debug(f"TP Order Parameters: {tp_params}")

        tp_response = self._place_order(tp_params)
        if tp_response and tp_response.get("success"):
            self.tp_order_ids[position_size] = tp_response["data"]
            logger.info("=" * 40)
            logger.info(f"[TP ORDER PLACED] Size: {position_size}, Price: {tp_price:.6f}")
            logger.info("=" * 40)
            log_json("TP_ORDER_PLACED", {
                "trade_id": self.current_trade_id,
                "position_size": position_size,
                "tp_price": tp_price,
                "filled_price": filled_price,
                "order_id": tp_response["data"]
            })
            # 状態を保存（TP注文発注後）
            self._save_trade_state()
            return True
        else:
            logger.error(f"Failed to place take profit order: {tp_response}")
            return False

    def _check_order_filled_retry(self, order_id, max_retries=5, sleep_sec=3):
        for attempt in range(1, max_retries + 1):
            status, filled_price = self._check_order_filled(order_id)
            if status not in ("unknown", ""):
                return (status, filled_price)
            logger.debug(f"fetch_order attempt {attempt}/{max_retries}")
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
        self.entry_price = None
        self.order_timestamp = None
        # トレードIDはクリアしない！（これが永続化の鍵）
        # self.current_trade_id = None
        self.order_lock_until = time.time() + 5  # キャンセル時にも5秒間ロック
        self.tp_order_ids = {}
        self._clear_local_cache()
        self._filled_logged = False
        self.open_position_side = None

    def _clear_local_cache(self):
        self._last_order_status = None
        self._last_status_check_time = 0

    def _is_entry_order_expired(self) -> bool:
        if not self.order_timestamp:
            return False

        t0 = datetime.fromisoformat(self.order_timestamp)
        now_ = datetime.utcnow()
        elapsed_sec = (now_ - t0).total_seconds()

        # 通常のタイムアウト条件チェック - タイムアウト時間未満ならFalse
        if elapsed_sec <= self.trade_logic.order_timeout_sec:
            return False

        try:
            # 部分約定の確認
            try:
                od = self.exchange.fetch_order(str(self.entry_order_id), self.ccxt_symbol)
                if od and od.get('filled', 0) > 0 and od.get('filled', 0) < od.get('amount', 0):
                    logger.info(f"部分約定検出: {od.get('filled')}/{od.get('amount')} が約定済み、タイムアウト延長")
                    return False  # 部分約定があればタイムアウトしない
            except Exception as e:
                logger.error(f"部分約定確認でエラー: {e}")
                # エラー時は通常のタイムアウト判定に進む

            # 以下、既存の市場価格に基づく延長条件
            if not self.current_market_price:
                logger.warning("No market price available for timeout extension check")
                return True

            order = self.exchange.fetch_order(str(self.entry_order_id), self.ccxt_symbol)
            if not order:
                return True

            entry_price = float(order.get('price', 0))

            if self.open_position_side == "SHORT":
                entry_base_price = entry_price - self.trade_logic.offset_entry - 0.00001
                logger.debug(
                    f"SHORT order timeout check: current={self.current_market_price:.6f}, base={entry_base_price:.6f}")
                return not (self.current_market_price >= entry_base_price)
            elif self.open_position_side == "LONG":
                entry_base_price = entry_price + self.trade_logic.offset_entry + 0.00001
                logger.debug(
                    f"LONG order timeout check: current={self.current_market_price:.6f}, base={entry_base_price:.6f}")
                return not (self.current_market_price <= entry_base_price)
            else:
                logger.error("Order type is undefined. Cancelling order.")
                return True
        except Exception as e:
            logger.error(f"Order timeout extension check failed: {e}")
            return True  # エラー時はタイムアウトとして扱う

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

    # === トレード状態の永続化機能（新規追加）===

    def _save_trade_state(self):
        """トレード状態をファイルに保存"""
        if not settings.PERSISTENCE_ENABLED:
            return

        try:
            state = {
                'trade_id': self.current_trade_id,
                'dynamic_lot_size': self.dynamic_lot_size,
                'open_position_side': self.open_position_side,
                'last_trade_time': time.time(),
                'tp_order_ids': self.tp_order_ids,
                'entry_price': self.entry_price
            }

            with open(settings.TRADE_STATE_FILE, 'w') as f:
                json.dump(state, f)

            logger.debug(f"Saved trade state: ID={self.current_trade_id}, lot={self.dynamic_lot_size}")
        except Exception as e:
            logger.error(f"Failed to save trade state: {e}")

    def _load_trade_state(self):
        """保存されたトレード状態を読み込む"""
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
        """起動時に保存された状態を復元"""
        if not settings.PERSISTENCE_ENABLED:
            return

        saved_state = self._load_trade_state()
        if not saved_state:
            return

        # リセットが必要かチェック
        if self._is_state_reset_needed(saved_state):
            logger.info("Martingale state reset due to timeout or missing data")
            self.reset_martingale()
            return

        # 状態を復元
        self.current_trade_id = saved_state.get('trade_id')
        self.dynamic_lot_size = saved_state.get('dynamic_lot_size', self.lot_size)
        self.open_position_side = saved_state.get('open_position_side')
        self.tp_order_ids = saved_state.get('tp_order_ids', {})
        self.entry_price = saved_state.get('entry_price')
        self.last_trade_time = saved_state.get('last_trade_time', time.time())

        logger.info(f"Restored trade state: ID={self.current_trade_id}, lot={self.dynamic_lot_size}")

    def _is_state_reset_needed(self, saved_state):
        """状態のリセットが必要かどうかを判断"""
        # 保存されたデータがない場合
        if not saved_state:
            return True

        # 必須データが欠けている場合
        if 'trade_id' not in saved_state or 'dynamic_lot_size' not in saved_state:
            return True

        # リセットタイムアウトが設定されている場合（0以外）
        if settings.MARTINGALE_RESET_TIMEOUT > 0:
            last_trade_time = saved_state.get('last_trade_time', 0)
            elapsed_sec = time.time() - last_trade_time

            # 設定された時間を超えた場合はリセット
            if elapsed_sec > settings.MARTINGALE_RESET_TIMEOUT:
                logger.info(
                    f"Martingale state reset: {elapsed_sec:.0f}s elapsed since last trade (timeout: {settings.MARTINGALE_RESET_TIMEOUT}s)")
                return True

        return False

    def reset_martingale(self):
        """マーチンゲールカウンターを明示的にリセット"""
        logger.info("Resetting martingale counter")

        # 基本ロットサイズにリセット
        self.dynamic_lot_size = self.lot_size

        # トレードIDをクリア
        self.current_trade_id = None

        # その他の状態をクリア
        self.entry_order_id = None
        self.entry_price = None
        self.order_timestamp = None
        self.tp_order_ids = {}
        self.open_position_side = None

        # 永続化が有効な場合は、リセットした状態を保存
        if settings.PERSISTENCE_ENABLED:
            try:
                state = {
                    'trade_id': None,
                    'dynamic_lot_size': self.lot_size,
                    'last_trade_time': time.time(),
                    'reset_time': time.time()
                }

                with open(settings.TRADE_STATE_FILE, 'w') as f:
                    json.dump(state, f)

                logger.debug("Martingale reset state saved")
            except Exception as e:
                logger.error(f"Failed to save reset state: {e}")