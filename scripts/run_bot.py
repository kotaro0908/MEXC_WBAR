#!/usr/bin/env python3
"""
run_bot.py
==========

MEXC_WBAR のライブ実行エントリ。

■ 主な役割
1. 環境変数・設定読み込み
2. DataHandler1m で 1 分足をストリーム取得
3. WBARSimpleStrategy でシグナル判定→OrderManager 経由で市場注文
4. WSListener で TP / SL fill を検知し StatsTracker / RiskGuard へ伝搬
5. stop_event が立ったら安全にシャットダウン

※ Windows では SelectorEventLoop を明示（Py3.12 互換）
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

# ---------- ロギング ---------- #
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    filename=LOG_DIR / "run_bot.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------- .env 読み込み ---------- #
from dotenv import load_dotenv

ENV_PATH = Path("config") / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    logger.warning(".env が見つかりません – 環境変数を直接参照します。")

# ---------- 外部モジュール ---------- #
from src.core.strategy import WBARSimpleStrategy
from src.core.ws_listener import WSListener
from src.monitor.stats_tracker import StatsTracker
from src.monitor.risk_guard import RiskGuard

# DataHandler1m が既に実装済み前提
from src.data.data_handler import DataHandler1m  # ← 名前が違う場合は修正


# ========================================================= #
#  asyncio イベントループ（Windows で SelectorLoop 強制）
# ========================================================= #
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# ========================================================= #
#  グローバルインスタンス
# ========================================================= #
SYMBOL = os.getenv("SYMBOL", "ETH_USDT")
LOT = os.getenv("LOT", "0.01")

strategy = WBARSimpleStrategy(symbol=SYMBOL, lot=LOT)
stats_tracker = StatsTracker()

stop_event = threading.Event()
risk_guard = RiskGuard(stop_event=stop_event)

# Balance 取得用 – ここではダミー。実装済み API に合わせて置き換え
def get_account_balance() -> float:
    return float(os.getenv("ACCOUNT_BALANCE_USDT", "1000"))


# ========================================================= #
#  WebSocket Listener – fill 時に Stats / Risk 更新
# ========================================================= #
def ws_thread() -> None:
    """
    別スレッドで WSListener を走らせ、約定をハンドリング。
    OrderManager.on_fill() → Strategy.callback_after_fill()
    という流れを想定し、Strategy から戻る pnl を受け取る。
    """
    class _ExtendedWS(WSListener):
        def __init__(self, strat: WBARSimpleStrategy):
            super().__init__(strat._om)
            self._strategy = strat

        def _on_local_fill(self, filled_order_id: str, pnl: float, side: str):
            """Strategy 側から呼び出される想定のコールバック"""
            stats_tracker.add_trade(side=side, pnl=pnl)
            risk_guard.on_trade(pnl=pnl, balance=get_account_balance())

        # Strategy から callback を登録
    ws = _ExtendedWS(strategy)
    try:
        ws.run_forever()
    except Exception as exc:
        logger.exception(f"WS thread exception: {exc}")
        stop_event.set()


# ========================================================= #
#  メイン async ループ – 1 分足を処理
# ========================================================= #
async def main_loop():
    dh = DataHandler1m(symbol=SYMBOL, warmup=29)
    await dh.initialize()

    # WS Listener スレッド起動
    threading.Thread(target=ws_thread, name="WSListener", daemon=True).start()

    while not stop_event.is_set():
        try:
            bar = await dh.get_next_bar()        # 1 分足確定を await
            direction = strategy.evaluate(bar)   # "LONG"/"SHORT"/None
            if direction:
                entry_id: Optional[str] = strategy.place_entry(direction)
                if entry_id:
                    logger.info(f"Entry sent: {entry_id}")

        except Exception as exc:
            logger.exception(f"Main loop error: {exc}")
            time.sleep(1)

    logger.info("Stop event received – shutting down.")


# ========================================================= #
#  Graceful shutdown helpers
# ========================================================= #
def _signal_handler(sig, frame):
    logger.info(f"Signal {sig} received, stopping…")
    stop_event.set()


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ========================================================= #
#  エントリポイント
# ========================================================= #
if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt – exiting.")
