#!/usr/bin/env python3
"""
RiskGuard
=========

当日損失が閾値を超えた / 連敗過多 の場合に
- Bot を強制停止（stop_event をセット）
- Notifier へ ERROR

呼び出し側（run_bot.py）で `risk_guard.on_trade(pnl)` を
TP / SL 約定直後に実行してください。
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Deque

from collections import deque

MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "-2"))  # 例 -2 (%)
MAX_CONSECUTIVE_LOSS = int(os.getenv("MAX_CONSECUTIVE_LOSS", 10))

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class RiskGuard:
    """日次損失・連敗数を監視し、超えたら停止イベントを立てる。"""

    def __init__(self, stop_event):
        self._stop_event = stop_event
        self._day = _dt.date.today()
        self._daily_pnl = 0.0
        self._consec_losses: Deque[float] = deque(maxlen=MAX_CONSECUTIVE_LOSS)

    def on_trade(self, pnl: float, balance: float) -> None:
        today = _dt.date.today()
        if today != self._day:
            # 新しい日になったらリセット
            self._day = today
            self._daily_pnl = 0.0
            self._consec_losses.clear()

        self._daily_pnl += pnl
        self._consec_losses.append(pnl)

        # 判定
        daily_loss_pct = (self._daily_pnl / balance) * 100
        consec_loss_cnt = sum(1 for x in self._consec_losses if x < 0)

        if daily_loss_pct <= MAX_DAILY_LOSS_PCT:
            logger.error(f"[RiskGuard] Daily loss {daily_loss_pct:.2f}% exceeds limit.")
            self._stop_event.set()

        if consec_loss_cnt >= MAX_CONSECUTIVE_LOSS:
            logger.error(f"[RiskGuard] {consec_loss_cnt} consecutive losses.")
            self._stop_event.set()
