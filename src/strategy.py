# log_utils.py
import os
import json
from datetime import datetime
from utils.logger import get_logger

logger = get_logger(__name__)


def log_json(event_name, data: dict):
    now = datetime.utcnow()
    date_str = now.strftime("%Y%m%d")
    logs_dir = "logs"
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    filename = os.path.join(logs_dir, f"trades_{date_str}.jsonl")
    log_data = {
        "trade_id": data.get("trade_id", f"T{now.strftime('%Y%m%d_%H%M%S')}"),
        "timestamp": now.isoformat(),
        "event": event_name,
        "data": data
    }
    line = json.dumps(log_data, ensure_ascii=False)
    logger.info(f"[LOG] {line}")
    with open(filename, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_trade_result(data: dict):
    now = datetime.utcnow()
    date_str = now.strftime("%Y%m%d")
    logs_dir = "logs"
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    filename = os.path.join(logs_dir, f"trade_results_{date_str}.jsonl")

    log_data = {
        "timestamp": now.isoformat(),
        "trade_id": data.get("trade_id"),
        "entry_time": data.get("entry_time"),
        "entry_price": data.get("entry_price"),
        "adx_value": data.get("adx_value"),
        "direction": data.get("direction"),  # "LONG" or "SHORT"
        "exit_type": data.get("exit_type"),  # "TP" or "SL"
        "exit_price": data.get("exit_price"),
        "pnl": data.get("pnl"),  # 価格差
        "bb_upper": data.get("bb_upper"),
        "bb_lower": data.get("bb_lower"),
        "bb_middle": data.get("bb_middle")
    }

    line = json.dumps(log_data, ensure_ascii=False)
    logger.info(f"[TRADE RESULT] {line}")
    with open(filename, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# strategy.py
import pandas as pd
from src.indicators import calculate_bollinger, calculate_adx
from utils.logger import get_logger
from config.settings import settings
import asyncio
from datetime import datetime

logger = get_logger(__name__)


class Strategy:
    def __init__(self):
        # オフセットやタイムアウトなどのパラメーター（設定値を利用）
        self.offset_entry = settings.ENTRY_PRICE_OFFSET
        self.offset_tp = settings.TP_AMOUNT
        self.offset_sl = settings.SL_AMOUNT
        self.order_timeout_sec = settings.ORDER_TIMEOUT_SEC

        # 市場データ履歴の初期化（データ型を明示的に指定）
        self.market_data_history = pd.DataFrame({
            "timestamp": pd.Series(dtype="datetime64[ns]"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64")
        })

        # タイムスタンプ重複チェック用
        self._last_processed_timestamp = None

    def update_market_data(self, market_data: dict):
        """
        市場データを更新し、重複を防ぐ

        Parameters:
            market_data (dict): timestamp, high, low, close を含む辞書
        """
        try:
            # タイムスタンプの重複チェック
            current_timestamp = pd.to_datetime(market_data.get("timestamp"))
            if self._last_processed_timestamp == current_timestamp:
                logger.debug(f"Skipping duplicate data for timestamp: {current_timestamp}")
                return

            # データの検証
            if not all(key in market_data for key in ["timestamp", "high", "low", "close"]):
                logger.error(f"Missing required keys in market_data: {market_data}")
                return

            # 1. 入力データの型を明示的に指定
            new_row = {
                "timestamp": current_timestamp,
                "high": float(market_data["high"]),
                "low": float(market_data["low"]),
                "close": float(market_data["close"])
            }

            # 2. データの論理チェック
            if new_row["high"] < new_row["low"]:
                logger.error(f"Invalid price data: high ({new_row['high']}) < low ({new_row['low']})")
                return

            # 3. 新しい DataFrame を作成
            new_row_df = pd.DataFrame([new_row])
            new_row_df = new_row_df.astype({
                "timestamp": "datetime64[ns]",
                "high": "float64",
                "low": "float64",
                "close": "float64"
            })

            # 4. データを結合
            self.market_data_history = pd.concat([self.market_data_history, new_row_df], ignore_index=True)

            # 5. 履歴を制限（最大1000行）
            if len(self.market_data_history) > 1000:
                self.market_data_history = self.market_data_history.tail(1000)

            self._last_processed_timestamp = current_timestamp

        except Exception as e:
            logger.error(f"Error updating market data: {str(e)}")
            return

    async def evaluate_and_execute(self, order_manager):
        """
        戦略の評価と実行

        Parameters:
            order_manager: 注文管理オブジェクト
        """
        try:
            # ADXとBBの両方に必要な最小データ量をチェック
            min_required_data = max(settings.BB_PERIOD, settings.ADX_PERIOD * 2)
            if len(self.market_data_history) < min_required_data:
                logger.debug(f"Insufficient market data history (<{min_required_data} rows).")
                return

            # ボリンジャーバンドの計算
            bollinger = calculate_bollinger(
                self.market_data_history,
                period=settings.BB_PERIOD,
                std_dev=settings.BB_STD_DEV
            )

            # ADXの計算
            adx_value = calculate_adx(
                self.market_data_history,
                period=settings.ADX_PERIOD
            )

            latest_price = self.market_data_history["close"].iloc[-1]
            lower_band = bollinger["lower"].iloc[-1]
            middle_band = bollinger["middle"].iloc[-1]
            upper_band = bollinger["upper"].iloc[-1]

            # 指標値の妥当性チェック
            if not (0 <= adx_value <= 100):
                logger.warning(f"Invalid ADX value: {adx_value}")
                return

            if not all(pd.notna([lower_band, middle_band, upper_band])):
                logger.warning("Invalid Bollinger Bands values detected")
                return

            # ログ出力
            logger.debug(f"Latest Price: {latest_price}")
            logger.debug(f"Bollinger Bands: Lower={lower_band}, Middle={middle_band}, Upper={upper_band}")
            logger.debug(f"ADX Value: {adx_value}")

            # エントリー条件チェック
            if settings.ADX_LOWER_THRESHOLD <= adx_value < settings.ADX_THRESHOLD:
                # ロングエントリー条件（lower_bandから指定ポイント上）
                long_threshold = lower_band + self.offset_entry
                # ショートエントリー条件（upper_bandから指定ポイント下）
                short_threshold = upper_band - self.offset_entry

                logger.debug(
                    f"LONG Entry condition check: latest_price {latest_price} <= long_threshold {long_threshold}")
                logger.debug(
                    f"SHORT Entry condition check: latest_price {latest_price} >= short_threshold {short_threshold}")

                trade_info = {
                    "entry_time": datetime.utcnow().isoformat(),
                    "entry_price": latest_price,
                    "adx_value": adx_value,
                    "bb_upper": upper_band,
                    "bb_lower": lower_band,
                    "bb_middle": middle_band
                }

                if latest_price <= long_threshold:
                    logger.info(f"Triggering LONG entry at price {latest_price}")
                    trade_info["direction"] = "LONG"
                    await order_manager.place_entry_order(side="LONG", trigger_price=latest_price,
                                                          trade_info=trade_info)
                elif latest_price >= short_threshold:
                    logger.info(f"Triggering SHORT entry at price {latest_price}")
                    trade_info["direction"] = "SHORT"
                    await order_manager.place_entry_order(side="SHORT", trigger_price=latest_price,
                                                          trade_info=trade_info)
                else:
                    logger.debug("No entry conditions met")
            else:
                logger.debug(f"ADX condition not met for entry (ADX not between {settings.ADX_LOWER_THRESHOLD} and {settings.ADX_THRESHOLD}).")

        except Exception as e:
            logger.error(f"Error in evaluate_and_execute: {str(e)}")
            return