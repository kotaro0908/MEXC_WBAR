#!/usr/bin/env python3
"""
DataHandler1m  ―  MEXC Futures の 1 分足フェッチを最小構成で
----------------------------------------------------------------
* initialize()   : 最新 (warmup+1) 本をロードしてキャッシュ
* get_next_bar() : 次の 1 分足が確定するまで await し、終値バーを返す

依存:
    pip install curl-cffi
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Dict, List

from curl_cffi import requests

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ─────────────────────────────────────────────
BASE_URL     = "https://contract.mexc.com"
INTERVAL     = "Min1"      # 1 m 足
MAX_RETRY    = 10
DEFAULT_WARM = 10          # ウォームアップ本数
# ─────────────────────────────────────────────


def _utc_floor_minute() -> dt.datetime:
    """UTC 現在時刻を秒以下 0 に丸めて返す"""
    now = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    return now.replace(second=0, microsecond=0)


class DataHandler1m:
    """MEXC の 1 m Kline を取得してキャッシュする軽量クラス"""

    def __init__(self, symbol: str, warmup: int = DEFAULT_WARM):
        self.symbol = symbol
        self._warm  = warmup
        self._cache: List[Dict] = []

    # ─────────────── public ─────────────── #

    async def initialize(self):
        """最新 warmup+1 本を取得してキャッシュ"""
        bars = self._fetch_bars(self._warm + 1)
        if not bars:
            raise RuntimeError("Failed to fetch warm-up bars.")
        self._cache = bars
        logger.info(f"Warmed up {len(bars)} bars.")

    async def get_next_bar(self) -> Dict:
        """次の 1 分足が確定するまで待機し、最新バー dict を返す"""
        now = _utc_floor_minute()
        await asyncio.sleep(60 - now.second + 1)

        bars = self._fetch_bars(2)
        if not bars:
            raise RuntimeError("Failed to fetch new bar.")

        latest = bars[-1]
        if latest["ts"] == self._cache[-1]["ts"]:
            return await self.get_next_bar()          # 同じ足なら再待機

        self._cache.append(latest)
        if len(self._cache) > self._warm + 1:
            self._cache.pop(0)
        return latest

    # ─────────────── internal ─────────────── #

    def _fetch_bars(self, limit: int) -> List[Dict]:
        """
        /contract/kline/{symbol}?interval=Min1&limit=N
        を叩いて直近 limit 本の OHLCV を返す。
        """
        url    = f"{BASE_URL}/api/v1/contract/kline/{self.symbol}"
        params = {"interval": INTERVAL, "limit": limit}

        for _ in range(MAX_RETRY):
            try:
                r = requests.get(url, params=params, timeout=10)
                data = r.json()

                # 成功判定
                if not (isinstance(data, dict) and data.get("success")):
                    time.sleep(1)
                    continue

                k = data["data"]                      # 列ごとの配列
                if len(k["time"]) < limit:
                    time.sleep(1)
                    continue

                bars = [
                    {
                        "ts":     k["time"] [-limit:][i],          # epoch 秒
                        "open":  float(k["open"] [-limit:][i]),
                        "high":  float(k["high"] [-limit:][i]),
                        "low":   float(k["low"]  [-limit:][i]),
                        "close": float(k["close"][-limit:][i]),
                        "volume":float(k["vol"]  [-limit:][i]),
                    }
                    for i in range(limit)
                ]
                return bars

            except Exception as e:
                logger.debug(f"Kline fetch retry fail: {e}")
                time.sleep(1)

        logger.error("All retries failed – no Kline data.")
        return []
