from __future__ import annotations
"""
run_bot.py – launch the bot with real-time WebSocket ticks → 1-second bars.

構成:
    DataHandlerWS  : MEXC `public.deal` を購読して 1-秒 OHLCV を生成
    Strategy       : エントリー判定
    OrderManager   : 発注 & マーチン管理
    OrderMonitor   : TP/SL 約定監視

起動:
    python run_bot.py   （事前に .env を設定）
"""

import asyncio
import signal
from types import SimpleNamespace

import ccxt

from config.settings import settings
from utils.logger import get_logger
from src.strategy import Strategy
from src.order_manager import OrderManager
from src.order_monitor import OrderMonitor
from src.data_handler_ws import DataHandlerWS

logger = get_logger(__name__)


################################################################################
# メイン async 関数
################################################################################
async def run_bot() -> None:
    # ── REST exchange (発注・口座情報用) ───────────────────────────────
    exchange = ccxt.mexc({
        "apiKey": settings.API_KEY,
        "secret": settings.API_SECRET,
        "options": {"defaultType": "future", "recvWindow": 60000},
        "enableRateLimit": True,
    })

    # ── Core modules ─────────────────────────────────────────────────
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

    # Strategy.evaluate_and_execute が data_handler.get_confirmed_data を呼ぶため
    dummy_dh = SimpleNamespace(get_confirmed_data=lambda: None)

    # ── callback: 1-秒バーを受け取るたび実行 ───────────────────────
    async def on_bar(bar: dict):
        strategy.update_market_data(bar)
        await strategy.evaluate_and_execute(om, dummy_dh)
        await monitor.run()

    # WebSocket DataHandler
    dh_ws = DataHandlerWS(settings.WS_SYMBOL, on_bar)

    # ── Graceful shutdown ───────────────────────────────────────────
    stop_event = asyncio.Event()

    def _stop():
        logger.warning("Received stop signal – shutting down…")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:          # Windows
            signal.signal(sig, lambda *_: _stop())

    logger.info("=== BOT STARTED (WebSocket mode) ===")

    # run DataHandlerWS until stop_event is triggered
    ws_task = asyncio.create_task(dh_ws.run())

    await stop_event.wait()
    ws_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await ws_task

    logger.info("=== BOT EXIT ===")


################################################################################
# entry-point
################################################################################
if __name__ == "__main__":
    import contextlib

    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass
