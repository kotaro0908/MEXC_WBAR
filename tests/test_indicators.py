import pandas as pd
import numpy as np
import pytest
from tests.indicators import calculate_bollinger, calculate_adx

def test_bollinger_constant():
    """
    終値が一定の場合、標準偏差が0となり、Lower, Middle, Upperがすべて終値と同じ値になるはず。
    """
    # 30行の一定データ: close=100
    df = pd.DataFrame({"close": [100] * 30})
    # period=20, std_dev=2.0 を指定
    result = calculate_bollinger(df, period=20, std_dev=2.0)
    # 最後の値をチェック（初期のNaNは無視）
    lower = result["lower"].iloc[-1]
    middle = result["middle"].iloc[-1]
    upper = result["upper"].iloc[-1]
    assert np.isclose(lower, 100), f"Expected lower band to be 100, got {lower}"
    assert np.isclose(middle, 100), f"Expected middle band to be 100, got {middle}"
    assert np.isclose(upper, 100), f"Expected upper band to be 100, got {upper}"

def test_bollinger_missing_close():
    """
    'close'カラムがない場合はエラー(ValueError)を出すはず。
    """
    df = pd.DataFrame({"open": [100] * 30})
    with pytest.raises(ValueError):
        calculate_bollinger(df)

def test_adx_constant():
    """
    高値・安値・終値が一定の場合、方向性移動量が0となりADXの計算結果がNaNとなるケースが多い。
    """
    df = pd.DataFrame({
        "high": [100] * 30,
        "low": [100] * 30,
        "close": [100] * 30
    })
    adx_value = calculate_adx(df, period=14)
    assert np.isnan(adx_value), f"Expected ADX to be NaN for constant data, got {adx_value}"

def test_adx_missing_columns():
    """
    ADX計算には 'high', 'low', 'close' の全カラムが必要。1つでも欠ければValueErrorとなるはず。
    """
    df = pd.DataFrame({"close": [100] * 30})
    with pytest.raises(ValueError):
        calculate_adx(df, period=14)
