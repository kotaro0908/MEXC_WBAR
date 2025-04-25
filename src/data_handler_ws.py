from __future__ import annotations
"""
DataHandlerWS – subscribe to MEXC Futures WS `public.deal` and build 1-second bars.
• endpoint: wss://contract.mexc.com/ws
• channel : {"method":"sub.deal","param":{"symbol":"SOL_USDT"}}
"""

import asyncio, json, time, websockets
from datetime import datetime, timezone
from collections import deque
from utils.logger import get_logger

logger = get_logger(__name__)


class DataHandlerWS:
    def __init__(self, symbol: str, on_bar):
        """
        :param symbol: e.g. 'SOL_USDT'
        :param on_bar: callback(dict) – called each second with confirmed bar
        """
        self.symbol = symbol
        self.on_bar = on_bar
        self.url = "wss://contract.mexc.com/ws"
        self.trades: deque[tuple[float, float]] = deque()  # (ts, price)
        self._bar_task: asyncio.Task | None = None

    async def _agg_loop(self):
        """Create 1-second bar from self.trades."""
        open_, high, low, vol = None, None, None, 0.0
        sec_start = int(time.time())
        while True:
            await asyncio.sleep(0.1)
            now = time.time()
            # move trades from deque to local while <= now
            while self.trades and self.trades[0][0] < now:
                ts, price = self.trades.popleft()
                if open_ is None:
                    open_ = high = low = price
                else:
                    high = max(high, price)
                    low = min(low, price)
                close = price
                vol += 1
            if int(now) > sec_start and open_ is not None:
                bar = {
                    "timestamp": datetime.fromtimestamp(sec_start, tz=timezone.utc).isoformat(),
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "vol": vol,
                    "is_confirmed": True,
                }
                await self.on_bar(bar)
                # reset
                sec_start = int(now)
                open_ = high = low = close = None
                vol = 0.0

    async def _ws_loop(self):
        sub_msg = json.dumps({
            "method": "sub.deal",
            "param": {"symbol": self.symbol},
        })
        async for ws in websockets.connect(self.url, ping_interval=15):
            try:
                await ws.send(sub_msg)
                async for msg in ws:
                    data = json.loads(msg)
                    if data.get("channel") != "push.deal":
                        continue
                    deal = data["data"][0]  # [price, vol, side, ts]
                    price, ts = float(deal[0]), float(deal[3]) / 1000
                    self.trades.append((ts, price))
            except Exception as e:
                logger.warning(f"WS error: {e}; reconnecting in 3s")
                await asyncio.sleep(3)

    async def run(self):
        self._bar_task = asyncio.create_task(self._agg_loop())
        await self._ws_loop()


# ——— usage example inside run_bot.py ———
#
# async def on_bar(bar):
#     strategy.update_market_data(bar)
#
# dh = DataHandlerWS("SOL_USDT", on_bar)
# asyncio.create_task(dh.run())
