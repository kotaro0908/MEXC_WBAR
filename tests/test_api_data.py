import pytest
import numpy as np
from config import settings
import ccxt


def test_ohlcv_data_has_variation():
    # ccxtを使ってMEXCの1分足OHLCVデータを取得する
    exchange = ccxt.mexc({
        'apiKey': settings.settings.API_KEY,
        'secret': settings.settings.API_SECRET,
        'enableRateLimit': True,
    })
    symbol = settings.settings.CCXT_SYMBOL
    # 例として50本分のデータを取得
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe="1m", limit=50)

    # キャンドルデータが取得できていることを確認
    assert ohlcv, "No OHLCV data returned from API."

    # 各キャンドルが6要素（timestamp, open, high, low, close, volume）を持つかチェック
    for candle in ohlcv:
        assert len(candle) == 6, f"Candle data format error: {candle}"

    # 高値と安値のリストを作成
    highs = [candle[2] for candle in ohlcv]
    lows = [candle[3] for candle in ohlcv]

    # 高値と安値に十分な変動があるか（標準偏差が0より大きいか）確認
    high_std = np.std(highs)
    low_std = np.std(lows)

    assert high_std > 0, f"High values are constant, std: {high_std}"
    assert low_std > 0, f"Low values are constant, std: {low_std}"
