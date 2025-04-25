from __future__ import annotations
"""
run_bot.py – main async event loop that wires together Strategy, OrderManager,
DataHandler (thin wrapper around exchange websocket / rest), and OrderMonitor.

Usage::
    python run_bot.py  # .env must be configured

Stop with Ctrl‑C.
"""

import asyncio
import signal
from datetime import datetime

from config.settings import settings
from utils.logger import get_logger

from src.strategy import Strategy
from src.order_manager import OrderManager
from src.order_monitor import OrderMonitor

logger = get_logger(__name__)

################################################################################
# Dummy minimal DataHandler (placeholder)
################################################################################

class DataHandler:
    """Grabs latest 1‑min klines via ccxt fetch_ohlcv. Replace with WS in prod."""

    def __init__(self, exchange, symbol: str):
        self.exchange = exchange
        self.symbol = symbol

    async def poll(self):
        # fetch last closed kline (1m)
        ohlcvs = self.exchange.fetch_ohlcv(self.symbol, timeframe="1m", limit=2)
        ts, o, h, l, c, v = ohlcvs[-2]  # last closed bar
        return {
            "timestamp": datetime.utcfromtimestamp(ts / 1_000).isoformat(),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "vol": v,
            "is_confirmed": True,
        }

################################################################################
# Main loop helpers
################################################################################

async def run_bot():
    import ccxt  # local import to avoid unused when testing

    ex = ccxt.mexc({
        "apiKey": settings.API_KEY,
        "secret": settings.API_SECRET,
        "options": {"defaultType": "future", "recvWindow": 60000},
        "enableRateLimit": True,
    })

    strategy = Strategy()
    om = OrderManager(
        trade_logic=strategy,
        ccxt_symbol=settings.CCXT_SYMBOL,
        ws_symbol=settings.WS_SYMBOL,
        lot_size=settings.LOT_SIZE,
        leverage=settings.LEVERAGE,
        uid=settings.UDI,
        api_key=settings.API_KEY,
        api_secret=settings.API_SECRET,
    )
    monitor = OrderMonitor(om)
    data_handler = DataHandler(ex, settings.CCXT_SYMBOL)

    stop_event = asyncio.Event()

    def _signal_handler():
        logger.warning("Received stop signal – shutting down...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows event loop
            signal.signal(sig, lambda *_: _signal_handler())

    logger.info("=== BOT STARTED ===")

    while not stop_event.is_set():
        try:
            # 1) poll market data & feed into strategy
            md = await data_handler.poll()
            strategy.update_market_data(md)

            # 2) evaluate strategy
            await strategy.evaluate_and_execute(om, data_handler)

            # 3) monitor open orders / positions
            await monitor.run()

        except Exception as e:
            logger.error(f"main loop error: {e}", exc_info=True)

        await asyncio.sleep(settings.POLLING_INTERVAL)

    logger.info("=== BOT EXIT ===")

################################################################################
# entry‑point
################################################################################

if __name__ == "__main__":
    asyncio.run(run_bot())
