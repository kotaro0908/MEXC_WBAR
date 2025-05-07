#!/usr/bin/env python3
"""
DataHandler1m
=============

MEXC Futures の 1 分足を簡易取得する非同期ハンドラ。
- `initialize()` でウォームアップ N 本ロード
- `get_next_bar()` で次の 1 分足が確定するまで待機し、終値バーを返す

依存:
    pip install curl-cffi pytz
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
from typing import Dict, List

from curl_cffi import requests
import pytz

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MEXC_KLINE_ENDPOINT = "https://contract.mexc.com/api/v1/contract/kline/{symbol}/60"  # 60 = 1m


def _utc_now_truncated() -> _dt.datetime:
    """秒以下を 0 にした UTC"""
    now = _dt.datetime.utcnow().replace(tzinfo=_dt.timezone.utc)
    return now.replace(second=0, microsecond=0)


class DataHandler1m:
    def __init__(self, symbol: str, warmup: int = 29):
        self.symbol = symbol
        self._warmup = warmup
        self._cache: List[Dict] = []

    # --------------------- Public --------------------- #
    async def initialize(self):
        """最新 N+1 本をロードしてキャッシュ"""
        bars = self._fetch_bars(self._warmup + 1)
        if not bars:
            raise RuntimeError("Failed to fetch warm-up bars.")
        self._cache = bars
        logger.info(f"Warmed up {len(bars)} bars.")

    async def get_next_bar(self) -> Dict:
        """
        次の 1 分足が確定するまで await し、最新バー dict を返す。

        Returns
        -------
        bar : dict
            {"ts": epoch_ms, "open": .., "high": .., "low": .., "close": .., "volume": ..}
        """
        # 次の分足が確定するまで待つ
        now = _utc_now_truncated()
        wait_sec = 60 - (now.replace(tzinfo=None).second)
        await asyncio.sleep(wait_sec + 1)

        # 直近 2 本取得して最新だけ返す
        bars = self._fetch_bars(2)
        if not bars:
            raise RuntimeError("Failed to fetch new bar.")

        latest = bars[-1]
        if latest["ts"] == self._cache[-1]["ts"]:
            # まだ同じバーなら次を待つ
            return await self.get_next_bar()

        self._cache.append(latest)
        if len(self._cache) > self._warmup + 1:
            self._cache.pop(0)
        return latest

    # --------------------- Internal --------------------- #
    def _fetch_bars(self, limit: int) -> List[Dict]:
        """REST で最新 limit 本の 1 分足を取得"""
        url = MEXC_KLINE_ENDPOINT.format(symbol=self.symbol)
        params = {"limit": limit}
        try:
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            if not data.get("success"):
                logger.error(f"Kline API error: {data}")
                return []
            klines = data["data"][-limit:]  # 保険でスライス
            bars = [
                {
                    "ts": k["time"],  # epoch ms
                    "open": float(k["open"]),
                    "high": float(k["high"]),
                    "low": float(k["low"]),
                    "close": float(k["close"]),
                    "volume": float(k["vol"]),
                }
                for k in klines
            ]
            return bars
        except Exception as exc:
            logger.exception(f"Kline fetch exception: {exc}")
            return []
