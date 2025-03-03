import asyncio
import ccxt
from config import settings
from utils.logger import get_logger
from datetime import datetime

logger = get_logger(__name__)

class DataHandler:
    def __init__(self):
        self.latest_data = {}  # 最新の1分足キャンドル情報を保持
        self.exchange = ccxt.mexc({
            'apiKey': settings.settings.API_KEY,
            'secret': settings.settings.API_SECRET,
            'enableRateLimit': True,
        })
        self.symbol = settings.settings.CCXT_SYMBOL

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
                dt = datetime.utcfromtimestamp(ts / 1000).isoformat()
                data_point = {
                    "timestamp": dt,
                    "high": high,
                    "low": low,
                    "close": close
                }
                initial_data.append(data_point)
            logger.info(f"Loaded {len(initial_data)} historical candles.")
            return initial_data
        except Exception as e:
            logger.error(f"Error loading initial OHLCV data: {e}")
            return []

    async def start(self):
        """
        定期的に最新の1分足キャンドルを取得し、最新データとして更新します。
        ポーリング間隔は10秒とし、前回のキャンドルと比較して新しいキャンドルがあれば更新します。
        """
        last_timestamp = None
        while True:
            try:
                ohlcv = self.exchange.fetch_ohlcv(self.symbol, timeframe="1m", limit=1)
                if ohlcv:
                    candle = ohlcv[0]
                    ts, open_, high, low, close, volume = candle
                    dt = datetime.utcfromtimestamp(ts / 1000).isoformat()
                    # 新しいキャンドルかどうかをチェック
                    if last_timestamp is None or dt > last_timestamp:
                        self.latest_data = {
                            "timestamp": dt,
                            "close": close,
                            "high": high,
                            "low": low
                        }
                        last_timestamp = dt
                        logger.info(f"New candle: {self.latest_data}")
                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"Error fetching latest candle: {e}")
                await asyncio.sleep(10)

    def get_latest_data(self):
        return self.latest_data
