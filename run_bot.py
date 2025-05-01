from __future__ import annotations
"""
run_bot.py – WBAR を “1 分足オンリー” で回す最小 BOT
────────────────────────────────────────────────────────
 • DataHandler1m : ccxt REST で確定 1 m 足を取得
 • Strategy      : 連続 2 本 + フィルタでエントリー
 • OrderManager  : 発注 & マーチン
 • OrderMonitor  : TP/SL 監視 + Notifier (Discord / Mail / Twilio)

起動:  python run_bot.py   (.env がロード済みの前提)
"""

# ──────────────────────────────────────────────────────────
# 0) 事前セットアップ
# ──────────────────────────────────────────────────────────
import warnings
warnings.simplefilter("ignore", category=FutureWarning)   # これを追加

# pandas concat の FutureWarning を黙らせる
warnings.filterwarnings(
    "ignore",
    message="The behavior of DataFrame concatenation.*FutureWarning"
)

import sys, asyncio, signal
from datetime import datetime, timezone
import ccxt

# Windows + aiodns 用: SelectorEventLoop を明示
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ─ 内部 import
from config.settings import settings
from utils.logger     import get_logger
from src.strategy     import Strategy
from src.order_manager import OrderManager
from src.order_monitor import OrderMonitor
from src.notifier      import Notifier

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────
# 1) DataHandler – 1 m OHLCV を取得
# ──────────────────────────────────────────────────────────
class DataHandler1m:
    """確定した 1-minute bar だけ返す軽量ハンドラ"""

    def __init__(self, exchange: ccxt.Exchange, symbol: str):
        self.exchange   = exchange
        self.symbol     = symbol
        self._last_ts_ms: int | None = None         # 重複排除
        self._dup_warn_ts: int | None = None        # duplicate ログ抑制

    async def poll(self) -> dict | None:
        loop = asyncio.get_running_loop()
        ohlcvs = await loop.run_in_executor(
            None,
            lambda: self.exchange.fetch_ohlcv(self.symbol, timeframe="1m", limit=2),
        )
        if not ohlcvs:
            return None

        ts_ms, o, h, l, c, v = ohlcvs[-2]           # 直近確定バー

        # ─ duplicate 判定 ─
        if ts_ms == self._last_ts_ms:
            if self._dup_warn_ts != ts_ms:          # 同じ ts で 1 回だけ
                logger.debug(f"[DataHandler] duplicate bar ts={ts_ms} – waiting")
                self._dup_warn_ts = ts_ms
            return None
        self._dup_warn_ts = None                    # new bar → リセット
        self._last_ts_ms  = ts_ms

        return {
            "timestamp": datetime.fromtimestamp(ts_ms / 1_000, tz=timezone.utc).isoformat(),
            "open": o, "high": h, "low": l, "close": c,
            "vol": v, "is_confirmed": True,
        }

    # Strategy.evaluate_and_execute 用ダミー
    def get_confirmed_data(self):
        return None


# ──────────────────────────────────────────────────────────
# 2) main coroutine
# ──────────────────────────────────────────────────────────
async def run_bot() -> None:
    ex = ccxt.mexc({
        "apiKey": settings.API_KEY,
        "secret": settings.API_SECRET,
        "options": {"defaultType": "future", "recvWindow": 60_000},
        "enableRateLimit": True,
    })

    strategy  = Strategy()
    notifier  = Notifier()
    om = OrderManager(
        trade_logic = strategy,
        ccxt_symbol = settings.CCXT_SYMBOL,
        ws_symbol   = settings.WS_SYMBOL,
        lot_size    = settings.LOT_SIZE,
        leverage    = settings.LEVERAGE,
        uid         = settings.UDI,
        api_key     = settings.API_KEY,
        api_secret  = settings.API_SECRET,
    )
    monitor = OrderMonitor(om, notifier=notifier)
    dh      = DataHandler1m(ex, settings.CCXT_SYMBOL)

    # ─ warm-up (直近 29 本) ─
    warm = ex.fetch_ohlcv(settings.CCXT_SYMBOL, timeframe="1m", limit=30)
    for ts, o, h, l, c, v in warm[:-1]:
        strategy.update_market_data({
            "timestamp": datetime.fromtimestamp(ts / 1_000, tz=timezone.utc).isoformat(),
            "open": o, "high": h, "low": l, "close": c, "vol": v, "is_confirmed": True,
        })
    logger.info(f"Prefilled {len(warm)-1} bars → instant start")

    # 起動通知 (INFO は Discord のみ送信)
    await notifier.send("INFO", "✅ WBAR bot started – waiting for first 1-minute bar")

    # ─ graceful shutdown ─
    stop = asyncio.Event()
    def _stop():
        logger.warning("Received stop signal – shutting down…")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:                # Windows fallback
            signal.signal(sig, lambda *_: _stop())

    logger.info("=== BOT STARTED (1-minute mode) ===")

    # ─ main loop ─
    while not stop.is_set():
        try:
            bar = await dh.poll()
            if bar:
                strategy.update_market_data(bar)
                await strategy.evaluate_and_execute(om, dh)
                await monitor.run()

        except Exception as e:
            logger.error(f"main loop error: {e}", exc_info=True)
            # ERROR 通知は Discord + Mail + Twilio
            await notifier.send("ERROR", f"main loop error: {e}")

        await asyncio.sleep(settings.POLLING_INTERVAL)   # default = 5 s

    logger.info("=== BOT EXIT ===")


# ──────────────────────────────────────────────────────────
# 3) entry-point
# ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass
