# src/strategy.py   ※全文

import os
import numpy as np
import pandas as pd
from datetime import datetime, timezone

from utils.logger import get_logger
from config.settings import settings

logger = get_logger(__name__)


# ------------------------------------------------------------
# ① フィルタ関数（vol spike / ATR band / ダウ保証）
#    ─ ここでは最小実装だけ置き、必要に応じて高度化してください
# ------------------------------------------------------------
def vol_spike(df: pd.DataFrame, ratio: float) -> bool:
    """出来高 5 本平均が 20 本平均 × ratio を超えていれば True"""
    if len(df) < 20:
        return False
    vol5 = df["vol"].tail(5).mean()
    vol20 = df["vol"].tail(20).mean()
    return vol5 > vol20 * ratio


def atr_band(atr_now: float, atr_base: float, minr: float, maxr: float) -> bool:
    """ATR が基準の min～max 倍内なら True"""
    if atr_base == 0:
        return False
    ratio = atr_now / atr_base
    return minr <= ratio <= maxr


def break_high_low(df: pd.DataFrame, window: int, direction: str) -> bool:
    """直近 window 本で高値（安値）を更新していれば True"""
    if len(df) < window:
        return False
    if direction == "LONG":
        return df["close"].iloc[-1] > df["high"].tail(window).max()
    else:
        return df["close"].iloc[-1] < df["low"].tail(window).min()


# ------------------------------------------------------------
# ② Strategy クラス
# ------------------------------------------------------------
class Strategy:
    def __init__(self):
        # ──────────── パラメータ
        self.offset_pct = settings.OFFSET_PCT / 100.0          # %→倍率
        self.martin_factor = settings.MARTIN_FACTOR
        self.max_level = settings.MAX_LEVEL

        self.consecutive_candles = settings.CONSECUTIVE_CANDLES

        # BOX（MA スロープ）判定用
        self.ma_period = settings.MA_PERIOD
        self.slope_period = settings.SLOPE_PERIOD
        self.slope_threshold = settings.SLOPE_THRESHOLD

        # 市場データ履歴
        self.market = pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "vol", "is_confirmed"]
        )

        self._last_ts = None

    # -------------- データ取り込み --------------
    def update_market_data(self, md: dict):
        """
        market_data = {
            "timestamp": "2025-04-25T14:00:00Z",
            "open": ..., "high": ..., "low": ..., "close": ..., "vol": ...,
            "is_confirmed": True/False
        }
        """
        try:
            ts = pd.to_datetime(md["timestamp"]).tz_convert(None)
            if self._last_ts == ts and not md.get("is_confirmed", False):
                return

            row = {
                "timestamp": ts,
                "open": float(md["open"]),
                "high": float(md["high"]),
                "low": float(md["low"]),
                "close": float(md["close"]),
                "vol": float(md.get("vol", 0)),
                "is_confirmed": bool(md.get("is_confirmed", False)),
            }
            self.market = pd.concat([self.market, pd.DataFrame([row])], ignore_index=True)
            if len(self.market) > 1500:
                self.market = self.market.tail(1500)
            self._last_ts = ts
        except Exception as e:
            logger.error(f"[update_market_data] {e}")

    # -------------- BOX 判定 --------------
    def _ma(self, period: int):
        if len(self.market) < period:
            return None
        confirmed = self.market[self.market["is_confirmed"]]
        if len(confirmed) < period:
            return None
        return confirmed["close"].rolling(period).mean()

    def _slope_deg(self, series: pd.Series):
        if series is None or series.isna().any():
            return 0
        y = series.values
        x = np.arange(len(y))
        slope, _ = np.polyfit(x, y, 1)
        return np.degrees(np.arctan(slope))

    def is_box(self):
        ma = self._ma(self.ma_period)
        if ma is None or len(ma) < self.slope_period:
            return False, 0.0
        slope = self._slope_deg(ma.tail(self.slope_period))
        return abs(slope) <= self.slope_threshold, slope

    # -------------- TP/SL オフセット計算 --------------
    def calc_offset(self, price: float, atr_now: float = None, atr_base: float = None):
        offset = price * self.offset_pct
        # ATR 連動オフセット
        if settings.USE_ATR_OFFSET and atr_now is not None and atr_base:
            ratio = max(settings.ATR_RATIO_MIN,
                        min(settings.ATR_RATIO_MAX, atr_now / atr_base))
            offset *= ratio
        return offset

    # -------------- エントリー判定 --------------
    def check_entry(self):
        if len(self.market[self.market["is_confirmed"]]) < self.consecutive_candles:
            return None

        # BOX 相場ならスキップ
        is_box, slope = self.is_box()
        if is_box:
            return None

        # 最新 N 本の確定ローソク足
        cdf = self.market[self.market["is_confirmed"]].sort_values("timestamp")
        latest = cdf.tail(self.consecutive_candles)

        bullish = all(latest["close"] > latest["open"])
        bearish = all(latest["close"] < latest["open"])
        if not (bullish or bearish):
            return None

        direction = "LONG" if bullish else "SHORT"

        # ─ フィルタ① 出来高スパイク
        if settings.USE_VOL_SPIKE and not vol_spike(cdf, settings.SPIKE_RATIO):
            return None

        # ─ フィルタ② ATR バンド
        atr_now = None
        atr_base = None
        if settings.USE_ATR_OFFSET or settings.USE_ATR_OFFSET:
            if len(cdf) >= 30:
                tr = np.maximum(
                    cdf["high"] - cdf["low"],
                    np.maximum(
                        abs(cdf["high"] - cdf["close"].shift()),
                        abs(cdf["low"] - cdf["close"].shift()),
                    ),
                )
                atr_now = tr.rolling(14).mean().iloc[-1]
                atr_base = tr.rolling(14).mean().rolling(30).mean().iloc[-1]
                if settings.USE_ATR_OFFSET is False:  # バンド判定のみ
                    if not atr_band(atr_now, atr_base,
                                    settings.ATR_RATIO_MIN, settings.ATR_RATIO_MAX):
                        return None

        # ─ フィルタ③ ダウ保証
        if settings.USE_DOW_BREAK and not break_high_low(cdf, settings.BREAK_WINDOW, direction):
            return None

        return direction, slope, atr_now, atr_base

    # -------------- メイン呼び出し --------------
    async def evaluate_and_execute(self, order_manager, data_handler):
        # データ更新
        confirmed = data_handler.get_confirmed_data()
        if confirmed:
            self.update_market_data(confirmed)

        # エントリー判定
        entry = self.check_entry()
        if entry is None:
            return
        direction, slope, atr_now, atr_base = entry

        if order_manager.has_open_position_or_order():
            return

        price = self.market[self.market["is_confirmed"]]["close"].iloc[-1]

        offset = self.calc_offset(price, atr_now, atr_base)
        tp = price + offset if direction == "LONG" else price - offset
        sl = price - offset if direction == "LONG" else price + offset

        # ロット = 基本 LOT_SIZE × マーチン段数倍率
        size = settings.LOT_SIZE * (settings.MARTIN_FACTOR ** order_manager.current_level)

        trade_info = {
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "entry_price": price,
            "direction": direction,
            "tp": tp,
            "sl": sl,
            "size": size,
            "slope": slope,
            "level": order_manager.current_level,
        }

        logger.info(f"[signal] {direction} price={price:.4f} tp={tp:.4f} sl={sl:.4f} "
                    f"level={order_manager.current_level}")

        await order_manager.place_entry_order(
            side=direction,
            qty=size,
            tp_price=tp,
            sl_price=sl,
            trade_info=trade_info,
        )
