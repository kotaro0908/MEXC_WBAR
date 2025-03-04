import asyncio
import ccxt
from config import settings
from utils.logger import get_logger
from datetime import datetime, timezone

logger = get_logger(__name__)


class DataHandler:
    def __init__(self):
        self.latest_data = {}  # 最新の1分足キャンドル情報を保持
        self.newest_confirmed_data = {}  # 最新の確定済み1分足キャンドル情報
        self.older_confirmed_data = {}  # 1つ前の確定済み1分足キャンドル情報
        self.exchange = ccxt.mexc({
            'apiKey': settings.settings.API_KEY,
            'secret': settings.settings.API_SECRET,
            'enableRateLimit': True,
        })
        self.symbol = settings.settings.CCXT_SYMBOL
        self.last_confirmed_timestamp = None

    async def load_initial_data(self, limit: int = 100):
        """
        1分足の過去OHLCVデータを取得します。
        各キャンドルは [timestamp, open, high, low, close, volume] の形式で返されます。
        timestampはISO形式の文字列に変換して保存します。
        """
        try:
            ohlcv = self.exchange.fetch_ohlcv(self.symbol, timeframe="1m", limit=limit)
            initial_data = []
            for candle in ohlcv:
                # candle の形式: [timestamp, open, high, low, close, volume]
                ts, open_, high, low, close, volume = candle
                candle_dt = datetime.utcfromtimestamp(ts / 1000)
                dt = candle_dt.isoformat()

                # 現在時刻との差で確定状態を判断
                now = datetime.now()
                time_diff_minutes = (now - candle_dt).total_seconds() / 60

                data_point = {
                    "timestamp": dt,
                    "open": open_,  # 始値を追加
                    "high": high,
                    "low": low,
                    "close": close,
                    "is_confirmed": time_diff_minutes >= 1  # 1分以上前のデータは確定済み
                }
                initial_data.append(data_point)

            logger.info(f"Loaded {len(initial_data)} historical candles.")

            # 直近の2つの確定済みデータを取得
            confirmed_candles = [d for d in initial_data if d["is_confirmed"]]
            if len(confirmed_candles) >= 2:
                # 時間順にソート
                confirmed_candles.sort(key=lambda x: x["timestamp"])
                # 最新の確定済みデータと1つ前の確定済みデータを保存
                self.newest_confirmed_data = confirmed_candles[-1]
                self.older_confirmed_data = confirmed_candles[-2]
                self.last_confirmed_timestamp = self.newest_confirmed_data["timestamp"]
                logger.info(f"Newest confirmed candle set to: {self.newest_confirmed_data}")
                logger.info(f"Older confirmed candle set to: {self.older_confirmed_data}")

            return initial_data
        except Exception as e:
            logger.error(f"Error loading initial OHLCV data: {e}")
            return []

    async def start(self):
        while True:
            try:
                # より多くのキャンドルを取得（最低3本、エラー回避のため）
                ohlcv = self.exchange.fetch_ohlcv(self.symbol, timeframe="1m", limit=5)

                # タイムスタンプでソートして確実に時系列順にする
                ohlcv.sort(key=lambda x: x[0])

                if len(ohlcv) >= 3:  # 最低3本のデータがあることを確認
                    # 最新のキャンドル（現在進行中の可能性があるため除外）
                    latest_candle = ohlcv[-1]
                    latest_ts, latest_open, latest_high, latest_low, latest_close, latest_vol = latest_candle
                    latest_time = datetime.utcfromtimestamp(latest_ts / 1000).replace(tzinfo=timezone.utc)

                    # 最新から1つ前のキャンドル（最新の確定済みと見なす）
                    newest_candle = ohlcv[-2]
                    newest_ts, newest_open, newest_high, newest_low, newest_close, newest_vol = newest_candle
                    newest_time = datetime.utcfromtimestamp(newest_ts / 1000).replace(tzinfo=timezone.utc)

                    # 最新から2つ前のキャンドル（より古い確定済みと見なす）
                    older_candle = ohlcv[-3]
                    older_ts, older_open, older_high, older_low, older_close, older_vol = older_candle
                    older_time = datetime.utcfromtimestamp(older_ts / 1000).replace(tzinfo=timezone.utc)

                    # 確定済みキャンドルを適切な変数に保存（時系列順）
                    self.older_confirmed_data = {
                        "timestamp": older_time.isoformat(),
                        "open": older_open,
                        "high": older_high,
                        "low": older_low,
                        "close": older_close,
                        "is_confirmed": True
                    }

                    self.newest_confirmed_data = {
                        "timestamp": newest_time.isoformat(),
                        "open": newest_open,
                        "high": newest_high,
                        "low": newest_low,
                        "close": newest_close,
                        "is_confirmed": True
                    }

                    logger.info(
                        f"Using confirmed candles - Older: {self.older_confirmed_data}, Newest: {self.newest_confirmed_data}")

                    # 最新キャンドル（現在進行中の可能性あり）も保存（市場価格更新用）
                    self.latest_data = {
                        "timestamp": latest_time.isoformat(),
                        "open": latest_open,
                        "high": latest_high,
                        "low": latest_low,
                        "close": latest_close
                    }

                    logger.debug(f"Latest market data: {self.latest_data}")
                else:
                    logger.warning("Not enough candle data received from exchange")

                # ポーリング間隔
                await asyncio.sleep(settings.settings.POLLING_INTERVAL)
            except Exception as e:
                logger.error(f"Error fetching candle data: {str(e)}")
                await asyncio.sleep(settings.settings.POLLING_INTERVAL)

    def get_latest_data(self):
        return self.latest_data

    def get_newest_confirmed_data(self):
        """最新の確定済みキャンドルデータを返す"""
        logger.debug(
            f"Returning newest confirmed data: {self.newest_confirmed_data.get('timestamp') if self.newest_confirmed_data else None}")
        return self.newest_confirmed_data

    def get_older_confirmed_data(self):
        """1つ前の確定済みキャンドルデータを返す"""
        logger.debug(
            f"Returning older confirmed data: {self.older_confirmed_data.get('timestamp') if self.older_confirmed_data else None}")
        return self.older_confirmed_data

    # 後方互換性のために古いメソッド名も維持
    def get_confirmed_data(self):
        """最新の確定済みキャンドルデータを返す（後方互換用）"""
        return self.get_newest_confirmed_data()

    def get_previous_confirmed_data(self):
        """1つ前の確定済みキャンドルデータを返す（後方互換用）"""
        return self.get_older_confirmed_data()