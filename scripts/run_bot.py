#!/usr/bin/env python3
"""
run_bot.py
==========

MEXC_WBAR ライブ実行エントリ
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# ──────────────────────────
#  ロギング（ファイル + コンソール）
# ──────────────────────────
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"

logging.basicConfig(
    level=logging.INFO,
    format=_fmt,
    handlers=[
        logging.FileHandler(LOG_DIR / "run_bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ──────────────────────────
#  .env 読み込み（フルパス固定）
# ──────────────────────────
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent   # プロジェクトルート
ENV_PATH = BASE_DIR / "config" / ".env"

if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
    logger.info(f".env loaded from {ENV_PATH}")
else:
    logger.warning(f".env not found at {ENV_PATH} – OS 環境変数を参照します。")

# ──────────────────────────
#  外部モジュール
# ──────────────────────────
from src.core.strategy import WBARSimpleStrategy
from src.core.ws_listener import WSListener
from src.monitor.stats_tracker import StatsTracker
from src.monitor.risk_guard import RiskGuard
from src.data.data_handler import DataHandler1m

# Windows は SelectorLoop 強制
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ──────────────────────────
#  グローバルインスタンス
# ──────────────────────────
SYMBOL = os.getenv("WS_SYMBOL", "SOL_USDT")
LOT    = os.getenv("LOT_SIZE", "0.01")

strategy      = WBARSimpleStrategy(symbol=SYMBOL, lot=LOT)
stats_tracker = StatsTracker()
stop_event    = threading.Event()
risk_guard    = RiskGuard(stop_event=stop_event)

def get_account_balance() -> float:
    return float(os.getenv("ACCOUNT_BALANCE_USDT", "1000"))

# ──────────────────────────
#  WebSocket Listener スレッド
# ──────────────────────────
def ws_thread() -> None:
    class _ExtendedWS(WSListener):
        def __init__(self, strat: WBARSimpleStrategy):
            super().__init__(strat._om)
            self._strategy = strat

        def _on_local_fill(self, filled_order_id: str, pnl: float, side: str):
            stats_tracker.add_trade(side=side, pnl=pnl)
            risk_guard.on_trade(pnl=pnl, balance=get_account_balance())

    try:
        _ExtendedWS(strategy).run_forever()
    except Exception as exc:
        logger.exception(f"WS thread exception: {exc}")
        stop_event.set()

# ──────────────────────────
#  メイン async ループ
# ──────────────────────────
async def main_loop():
    dh = DataHandler1m(symbol=SYMBOL, warmup=10)
    await dh.initialize()

    threading.Thread(target=ws_thread, name="WSListener", daemon=True).start()

    while not stop_event.is_set():
        try:
            bar = await dh.get_next_bar()
            direction = strategy.evaluate(bar)           # "LONG"/"SHORT"/None
            if direction:
                entry_id: Optional[str] = strategy.place_entry(direction)
                if entry_id:
                    logger.info(f"Entry sent: {entry_id}")
            else:
                logger.info("No signal – wait next bar")

        except Exception as exc:
            logger.exception(f"Main loop error: {exc}")
            time.sleep(1)

    logger.info("Stop event received – shutting down.")

# ──────────────────────────
#  Graceful shutdown
# ──────────────────────────
def _signal_handler(sig, frame):
    logger.info(f"Signal {sig} received – stopping …")
    stop_event.set()

signal.signal(signal.SIGINT,  _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

# ──────────────────────────
#  エントリポイント
# ──────────────────────────
if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt – exiting.")
