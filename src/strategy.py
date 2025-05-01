from __future__ import annotations
"""
strategy.py – WBAR のシグナル判定ロジック
"""

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# ①  フィルタ関数（ vol-spike / ATR バンド / 高値・安値ブレイク）
# --------------------------------------------------------------------------- #
def vol_spike(df: pd.DataFrame, ratio: float) -> bool:
    """出来高 5 本平均が 20 本平均 × ratio を超えているか"""
    if len(df) < 20:
        return False
    return df["vol"].tail(5).mean() > df["vol"].tail(20).mean() * ratio


def atr_band(now: float, base: float, min_r: float, max_r: float) -> bool:
    """ATR が基準 ATR の min_r〜max_r 倍内か"""
    if base == 0:
        return False
    r = now / base
    return min_r <= r <= max_r


def break_high_low(df: pd.DataFrame, window: int, direction: str) -> bool:
    """直近 window 本で高値（安値）を更新しているか"""
    if len(df) < window:
        return False
    if direction == "LONG":
        return df["close"].iloc[-1] > df["high"].tail(window).max()
    return df["close"].iloc[-1] < df["low"].tail(window).min()


# --------------------------------------------------------------------------- #
# ② Strategy
# --------------------------------------------------------------------------- #
class Strategy:
    def __init__(self):
        # 主要パラメータ
        self.offset_pct        = settings.OFFSET_PCT / 100.0
        self.consecutive_candles = settings.CONSECUTIVE_CANDLES

        # BOX 判定
        self.ma_period       = settings.MA_PERIOD
        self.slope_period    = settings.SLOPE_PERIOD
        self.slope_threshold = settings.SLOPE_THRESHOLD

        # 市場データ（DataFrame）
        self.market = pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "vol", "is_confirmed"]
        )
        self._last_ts = None

    # ----------------------- データ取り込み ----------------------- #
    def update_market_data(self, md: dict):
        try:
            ts = pd.to_datetime(md["timestamp"]).tz_convert(None)
            if self._last_ts == ts and not md.get("is_confirmed", False):
                return

            self.market = pd.concat(
                [
                    self.market,
                    pd.DataFrame(
                        {
                            "timestamp": [ts],
                            "open":  [float(md["open"])],
                            "high":  [float(md["high"])],
                            "low":   [float(md["low"])],
                            "close": [float(md["close"])],
                            "vol":   [float(md.get("vol", 0))],
                            "is_confirmed": [bool(md.get("is_confirmed", False))],
                        }
                    ),
                ],
                ignore_index=True,
            )

            # 直近 1500 本だけ残す
            if len(self.market) > 1500:
                self.market = self.market.tail(1500)

            self._last_ts = ts
        except Exception as e:
            logger.error(f"[update_market_data] {e}", exc_info=True)

    # ----------------------- BOX 判定 ----------------------- #
    def _ma(self, period: int):
        if len(self.market) < period:
            return None
        confirmed = self.market[self.market["is_confirmed"]]
        if len(confirmed) < period:
            return None
        return confirmed["close"].rolling(period).mean()

    def _slope_deg(self, series: pd.Series):
        if series is None or series.isna().any():
            return 0.0
        y = series.values
        x = np.arange(len(y))
        slope, _ = np.polyfit(x, y, 1)
        return math.degrees(math.atan(slope))

    def is_box(self) -> tuple[bool, float]:
        ma = self._ma(self.ma_period)
        if ma is None or len(ma) < self.slope_period:
            return False, 0.0
        slope = self._slope_deg(ma.tail(self.slope_period))
        return abs(slope) <= self.slope_threshold, slope

    # ----------------------- TP/SL 幅 ----------------------- #
    def calc_offset(self, price: float, atr_now: float | None = None, atr_base: float | None = None) -> float:
        offset = price * self.offset_pct
        if settings.USE_ATR_OFFSET and atr_now is not None and atr_base:
            ratio = max(settings.ATR_RATIO_MIN,
                        min(settings.ATR_RATIO_MAX, atr_now / atr_base))
            offset *= ratio
        return offset

    # ----------------------- エントリー判定 ----------------------- #
    def check_entry(self):
        confirmed = self.market[self.market["is_confirmed"]]
        if len(confirmed) < self.consecutive_candles:
            return None

        is_box, slope = self.is_box()
        if is_box:
            return None

        latest = confirmed.tail(self.consecutive_candles)
        bullish = all(latest["close"] > latest["open"])
        bearish = all(latest["close"] < latest["open"])
        if not (bullish or bearish):
            return None

        direction = "LONG" if bullish else "SHORT"

        # vol spike
        if settings.USE_VOL_SPIKE and not vol_spike(confirmed, settings.SPIKE_RATIO):
            return None

        # ATR & DOW フィルタ
        atr_now = atr_base = None
        if settings.USE_ATR_OFFSET or settings.USE_DOW_BREAK:
            if len(confirmed) >= 30:
                tr = np.maximum(
                    confirmed["high"] - confirmed["low"],
                    np.maximum(
                        abs(confirmed["high"] - confirmed["close"].shift()),
                        abs(confirmed["low"] - confirmed["close"].shift()),
                    ),
                )
                atr_now  = tr.rolling(14).mean().iloc[-1]
                atr_base = tr.rolling(14).mean().rolling(30).mean().iloc[-1]

            if settings.USE_DOW_BREAK and not break_high_low(confirmed, settings.BREAK_WINDOW, direction):
                return None

            if (not settings.USE_ATR_OFFSET) and atr_now and atr_base:
                if not atr_band(atr_now, atr_base,
                                settings.ATR_RATIO_MIN, settings.ATR_RATIO_MAX):
                    return None

        return direction, slope, atr_now, atr_base

    # ----------------------- メイン呼び出し ----------------------- #
    async def evaluate_and_execute(self, order_manager, data_handler):
        # data_handler 由来の “確定バー” があれば取り込む
        if (c := getattr(data_handler, "get_confirmed_data", lambda: None)()):
            self.update_market_data(c)

        entry = self.check_entry()
        if entry is None:
            return
        direction, slope, atr_now, atr_base = entry

        if order_manager.has_open_position_or_order():
            return

        price   = self.market[self.market["is_confirmed"]]["close"].iloc[-1]
        offset  = self.calc_offset(price, atr_now, atr_base)
        tp      = price + offset if direction == "LONG" else price - offset
        sl      = price - offset if direction == "LONG" else price + offset

        logger.info(
            f"[signal] {direction} price={price:.4f} tp={tp:.4f} sl={sl:.4f} "
            f"level={order_manager.current_level}"
        )

        trade_info = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "dir": direction,
            "entry": price,
            "tp": tp,
            "sl": sl,
            "slope": slope,
            "level": order_manager.current_level,
        }

        # ★ OrderManager は dynamic_lot を内部で計算するので qty 等は渡さない
        await order_manager.place_entry_order(
            side=direction,
            trigger_price=price,
            trade_info=trade_info,
        )
