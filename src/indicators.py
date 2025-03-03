import pandas as pd
import pandas_ta as ta
from utils.logger import get_logger

logger = get_logger(__name__)


def calculate_bollinger(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    """
    'close' カラムをもとにBollinger Bandsを計算します。
    結果は 'lower', 'middle', 'upper' の3カラムを持つDataFrameとして返され、
    欠損値はbackward fillで補完します。
    Parameters:
        df (pd.DataFrame): 少なくとも 'close' カラムを含むDataFrame
        period (int): 移動平均期間（デフォルト20）
        std_dev (float): 標準偏差の倍率（デフォルト2.0）
    Returns:
        pd.DataFrame: Bollinger Bandsの値
    """
    if "close" not in df.columns:
        raise ValueError("DataFrame must contain a 'close' column")
    if len(df) < period:
        raise ValueError(f"Insufficient data for Bollinger Bands calculation. Need at least {period} periods.")
    bb = ta.bbands(df["close"], length=period, std=std_dev)
    result = pd.DataFrame({
        "lower": bb[f"BBL_{period}_{std_dev}"],
        "middle": bb[f"BBM_{period}_{std_dev}"],
        "upper": bb[f"BBU_{period}_{std_dev}"]
    })
    # 欠損値がある場合、直近の有効値で埋める（backward fill）
    result = result.bfill()
    return result


def calculate_adx(df: pd.DataFrame, period: int = 14) -> float:
    """
    ADX（Average Directional Index）を計算します。
    Parameters:
        df (pd.DataFrame): 'high', 'low', 'close' カラムを含むDataFrame
        period (int): ADXの計算期間（デフォルト14）
    Returns:
        float: 最新のADX値
    Raises:
        ValueError: データ不足、無効なデータ、計算エラーの場合
    """
    required_columns = ["high", "low", "close"]
    if not all(col in df.columns for col in required_columns):
        raise ValueError("DataFrame must contain 'high', 'low', and 'close' columns")

    # データのバリデーション
    if df[required_columns].isna().any().any():
        raise ValueError("Data contains NaN values")

    # 価格の論理チェック
    if (df["high"] < df["low"]).any():
        raise ValueError("Invalid data: high price is lower than low price")

    # ADXの計算に必要な最小データ量をチェック
    min_required_data = period * 2
    if len(df) < min_required_data:
        raise ValueError(f"Insufficient data for ADX calculation. Need at least {min_required_data} periods.")

    # データが時系列順になっていることを確認
    if not df.index.is_monotonic_increasing:
        df = df.sort_index()

    try:
        # ADXの計算
        adx = ta.adx(df["high"], df["low"], df["close"], length=period)

        # pandas_taのADX出力の詳細をログ
        logger.debug(f"ADX calculation columns: {adx.columns}")
        logger.debug(f"ADX raw values: {adx.tail()}")

        # カラム名をより柔軟に処理
        adx_column = next(col for col in adx.columns if 'ADX' in col)
        adx_series = adx[adx_column]

        # 計算過程の値もログ
        logger.debug(f"Full ADX series: {adx_series.tail()}")
        logger.debug(f"+DI values: {adx['DMP_14'].tail()}")
        logger.debug(f"-DI values: {adx['DMN_14'].tail()}")

        latest_adx = adx_series.iloc[-1]
        return float(latest_adx)

    except Exception as e:
        logger.error(f"Error calculating ADX: {str(e)}")
        raise