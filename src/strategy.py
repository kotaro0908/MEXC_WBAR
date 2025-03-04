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
            "close": pd.Series(dtype="float64"),
            "is_confirmed": pd.Series(dtype="bool")
        })

        # タイムスタンプ重複チェック用
        self._last_processed_timestamp = None

    def update_market_data(self, market_data: dict):
        """
        市場データを更新し、重複を防ぐ
        """
        try:
            # 確定済みフラグがなければデフォルトでFalseにする
            is_confirmed = market_data.get("is_confirmed", False)

            # タイムゾーン情報を処理
            if 'timestamp' in market_data:
                # タイムゾーン情報を含む場合は除去する
                if '+' in market_data['timestamp']:
                    # タイムゾーン情報を除去してから変換
                    timestamp_str = market_data['timestamp'].split('+')[0]
                    current_timestamp = pd.to_datetime(timestamp_str)
                else:
                    current_timestamp = pd.to_datetime(market_data.get("timestamp"))
            else:
                logger.error("Missing timestamp in market data")
                return

            if self._last_processed_timestamp == current_timestamp and not is_confirmed:
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
                "close": float(market_data["close"]),
                "is_confirmed": is_confirmed
            }

            if new_row["high"] < new_row["low"]:
                logger.error(f"Invalid price data: high ({new_row['high']}) < low ({new_row['low']})")
                return

            # 既存のデータに同じタイムスタンプの行があれば、確定済みのデータで更新
            if is_confirmed and not self.market_data_history.empty:
                same_timestamp_idx = self.market_data_history.index[
                    self.market_data_history['timestamp'] == current_timestamp
                    ]
                if len(same_timestamp_idx) > 0:
                    for idx in same_timestamp_idx:
                        self.market_data_history.at[idx, 'open'] = new_row['open']
                        self.market_data_history.at[idx, 'high'] = new_row['high']
                        self.market_data_history.at[idx, 'low'] = new_row['low']
                        self.market_data_history.at[idx, 'close'] = new_row['close']
                        self.market_data_history.at[idx, 'is_confirmed'] = True
                    logger.debug(f"Updated existing data with confirmed data: {new_row}")
                    return

            new_row_df = pd.DataFrame([new_row])
            new_row_df = new_row_df.astype({
                "timestamp": "datetime64[ns]",
                "open": "float64",
                "high": "float64",
                "low": "float64",
                "close": "float64",
                "is_confirmed": "bool"
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
        確定済みの連続した同方向のローソク足を検出してエントリー条件を判定する
        追加条件：連続陽線/陰線ではキャンドル間の連続性も確認する
        """
        if len(self.market_data_history) < self.consecutive_candles:
            logger.debug(f"Not enough candles for entry conditions. Need at least {self.consecutive_candles}.")
            return None

        # 確定済みのローソク足のみを選択
        confirmed_data = self.market_data_history[self.market_data_history['is_confirmed'] == True]

        if len(confirmed_data) < self.consecutive_candles:
            logger.debug(f"Not enough confirmed candles. Need at least {self.consecutive_candles}.")
            return None

        # 時間順にソート
        sorted_data = confirmed_data.sort_values('timestamp')
        # 最新の確定済みローソク足を取得
        latest_candles = sorted_data.tail(self.consecutive_candles).reset_index(drop=True)

        # ログ出力
        for i, (_, candle) in enumerate(latest_candles.iterrows()):
            candle_direction = 'bullish' if candle['close'] > candle['open'] else (
                'bearish' if candle['close'] < candle['open'] else 'neutral')
            logger.debug(
                f"Confirmed Candle {i + 1}: ts={candle['timestamp']}, open={candle['open']}, close={candle['close']}, direction={candle_direction}")

        # 全てのローソク足が陽線かどうかチェック
        all_bullish = True
        # 全てのローソク足が陰線かどうかチェック
        all_bearish = True

        # 連続性チェック - より明示的に実装
        continuous_price_movement = True

        for i in range(len(latest_candles)):
            candle = latest_candles.iloc[i]

            # 陽線・陰線チェック
            if candle['close'] <= candle['open']:  # 陽線でない
                all_bullish = False
            if candle['close'] >= candle['open']:  # 陰線でない
                all_bearish = False

            # 連続性チェック (i > 0 の場合のみ)
            if i > 0:
                prev_candle = latest_candles.iloc[i - 1]

                # ロングの場合: 現在のローソク足の始値が前のローソク足の終値以上であること
                if all_bullish and candle['open'] < prev_candle['close']:
                    continuous_price_movement = False
                    logger.debug(
                        f"Price continuity broken for bullish trend: {prev_candle['close']} -> {candle['open']}")

                # ショートの場合: 現在のローソク足の始値が前のローソク足の終値以下であること
                if all_bearish and candle['open'] > prev_candle['close']:
                    continuous_price_movement = False
                    logger.debug(
                        f"Price continuity broken for bearish trend: {prev_candle['close']} -> {candle['open']}")

        # 判定結果のログ出力
        logger.debug(
            f"All candles bullish: {all_bullish}, All candles bearish: {all_bearish}, Price continuity: {continuous_price_movement}")

        # エントリー条件の判定
        if all_bullish and continuous_price_movement:
            logger.info("LONG entry condition met: All candles are bullish with price continuity")
            return "LONG"
        elif all_bearish and continuous_price_movement:
            logger.info("SHORT entry condition met: All candles are bearish with price continuity")
            return "SHORT"
        else:
            return None

    async def evaluate_and_execute(self, order_manager, data_handler):
        """
        戦略の評価と実行
        """
        try:
            # 確定済みデータを取得して更新
            confirmed_data = data_handler.get_confirmed_data()
            if confirmed_data:
                self.update_market_data(confirmed_data)

            if len(self.market_data_history) < self.consecutive_candles:
                logger.debug(f"Insufficient market data history (<{self.consecutive_candles} rows).")
                return

            # 既存のポジションまたはオープンオーダーがある場合は評価をスキップ
            if order_manager.has_open_position_or_order():
                logger.debug("Position or order exists - skipping strategy evaluation")
                return

            # 連続ローソク足でエントリー条件をチェック
            direction = self.check_entry_conditions()
            logger.debug(f"Entry conditions evaluated: {direction}")

            if direction is None:
                logger.debug("No entry conditions met")
                return

            # 最新の確定済み価格を取得
            confirmed_data = self.market_data_history[self.market_data_history['is_confirmed'] == True]
            sorted_data = confirmed_data.sort_values('timestamp')
            latest_price = sorted_data["close"].iloc[-1]

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