import pandas as pd
from utils.logger import get_logger
from config.settings import settings
import asyncio
from datetime import datetime

logger = get_logger(__name__)


class Strategy:
    def __init__(self):
        # オフセットやタイムアウトなどのパラメーター（設定値を利用）
        self.offset_tp = settings.TP_AMOUNT
        self.offset_sl = settings.SL_AMOUNT
        self.order_timeout_sec = settings.ORDER_TIMEOUT_SEC

        # 連続ローソク足の本数設定
        self.consecutive_candles = settings.CONSECUTIVE_CANDLES

        # 市場データ履歴の初期化（データ型を明示的に指定）
        self.market_data_history = pd.DataFrame({
            "timestamp": pd.Series(dtype="datetime64[ns]"),
            "open": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64")
        })

        # タイムスタンプ重複チェック用
        self._last_processed_timestamp = None

    def update_market_data(self, market_data: dict):
        """
        市場データを更新し、重複を防ぐ
        """
        try:
            current_timestamp = pd.to_datetime(market_data.get("timestamp"))
            if self._last_processed_timestamp == current_timestamp:
                logger.debug(f"Skipping duplicate data for timestamp: {current_timestamp}")
                return

            # 必須キーの検証
            if not all(key in market_data for key in ["timestamp", "open", "high", "low", "close"]):
                logger.error(f"Missing required keys in market_data: {market_data}")
                return

            new_row = {
                "timestamp": current_timestamp,
                "open": float(market_data["open"]),
                "high": float(market_data["high"]),
                "low": float(market_data["low"]),
                "close": float(market_data["close"])
            }

            if new_row["high"] < new_row["low"]:
                logger.error(f"Invalid price data: high ({new_row['high']}) < low ({new_row['low']})")
                return

            new_row_df = pd.DataFrame([new_row])
            new_row_df = new_row_df.astype({
                "timestamp": "datetime64[ns]",
                "open": "float64",
                "high": "float64",
                "low": "float64",
                "close": "float64"
            })

            # データを連結
            self.market_data_history = pd.concat([self.market_data_history, new_row_df], ignore_index=True)
            # 履歴を最大1000行に制限
            if len(self.market_data_history) > 1000:
                self.market_data_history = self.market_data_history.tail(1000)

            self._last_processed_timestamp = current_timestamp

        except Exception as e:
            logger.error(f"Error updating market data: {str(e)}")
            return

    def check_entry_conditions(self):
        """
        連続した同方向のローソク足を検出してエントリー条件を判定する
        """
        if len(self.market_data_history) < self.consecutive_candles:
            logger.debug(f"Not enough candles for entry conditions. Need at least {self.consecutive_candles}.")
            return None

        sorted_data = self.market_data_history.sort_values('timestamp')
        latest_candles = sorted_data.tail(self.consecutive_candles)

        # 各ローソク足の方向判定
        bullish_candles = latest_candles['close'] > latest_candles['open']
        bearish_candles = latest_candles['close'] < latest_candles['open']

        # 全てのキャンドルが同じ方向かチェック
        all_bullish = bullish_candles.all()
        all_bearish = bearish_candles.all()

        # ログ出力
        for i, (_, candle) in enumerate(latest_candles.iterrows()):
            candle_direction = 'bullish' if candle['close'] > candle['open'] else (
                'bearish' if candle['close'] < candle['open'] else 'neutral')
            logger.debug(
                f"Candle {i + 1}: ts={candle['timestamp']}, open={candle['open']}, close={candle['close']}, direction={candle_direction}")

        # エントリー条件の判定
        if all_bullish:
            return "LONG"
        elif all_bearish:
            return "SHORT"
        else:
            return None

    async def evaluate_and_execute(self, order_manager):
        """
        戦略の評価と実行
        """
        try:
            if len(self.market_data_history) < self.consecutive_candles:
                logger.debug(f"Insufficient market data history (<{self.consecutive_candles} rows).")
                return

            # 既存のポジションまたはオープンオーダーがある場合は評価をスキップ
            if order_manager.has_open_position_or_order():
                logger.debug("Position or order exists - skipping strategy evaluation")
                return

            # 最新の価格を取得
            latest_price = self.market_data_history.sort_values('timestamp')["close"].iloc[-1]

            # 連続ローソク足でエントリー条件をチェック
            direction = self.check_entry_conditions()
            logger.debug(f"Entry conditions evaluated: {direction}")

            if direction is None:
                logger.debug("No entry conditions met")
                return

            # トレード情報の準備
            trade_info = {
                "entry_time": datetime.utcnow().isoformat(),
                "entry_price": latest_price,
                "direction": direction
            }

            logger.info(
                f"{direction} entry signal: {self.consecutive_candles} consecutive {direction.lower()} candles detected")
            await order_manager.place_entry_order(side=direction, trigger_price=latest_price, trade_info=trade_info)

        except Exception as e:
            logger.error(f"Error in evaluate_and_execute: {str(e)}")
            return