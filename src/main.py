import asyncio
import os
from config import settings
from src.data_handler import DataHandler
from src.strategy import Strategy
from src.order_manager import OrderManager
from src.monitor import BotMonitor  # 監視モジュール
from utils.logger import get_logger
import pandas as pd

logger = get_logger(__name__)

def check_persistence():
    state_file = settings.settings.TRADE_STATE_FILE
    if os.path.exists(state_file):
        answer = input("保存されたトレード状態が見つかりました。続行しますか？（Y/N）: ").strip().upper()
        if answer != "Y":
            os.remove(state_file)
            print("保存状態をリセットしました。新規で再開します。")
        else:
            print("保存状態を読み込んで再開します。")

async def main():
    # 起動時に永続化ファイルの存在をチェック
    check_persistence()

    logger.info("Starting BOT...")

    # BOT監視モジュールの初期化
    max_consecutive_losses = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))
    bot_monitor = BotMonitor(max_consecutive_losses=max_consecutive_losses)

    # タイムスタンプ重複チェック用のセット
    processed_timestamps = set()

    # コンポーネント初期化
    data_handler = DataHandler()
    strategy = Strategy()
    order_manager = OrderManager(
        trade_logic=strategy,
        ccxt_symbol=settings.settings.CCXT_SYMBOL,
        ws_symbol=settings.settings.WS_SYMBOL,
        lot_size=settings.settings.LOT_SIZE,
        leverage=settings.settings.LEVERAGE,
        uid=settings.settings.UDI,
        api_key=settings.settings.API_KEY,
        api_secret=settings.settings.API_SECRET,
        position_threshold=settings.settings.POSITION_THRESHOLD,
        monitor=bot_monitor  # 監視モジュールを渡す
    )

    try:
        # 初期データのロードと重複チェック
        initial_data = await data_handler.load_initial_data(limit=100)
        validated_initial_data = []
        for candle in initial_data:
            timestamp = pd.to_datetime(candle.get("timestamp"))
            if timestamp not in processed_timestamps:
                processed_timestamps.add(timestamp)
                validated_initial_data.append(candle)

        # バリデーション済みの初期データを戦略に反映
        for candle in validated_initial_data:
            strategy.update_market_data(candle)

        logger.debug(f"Initial market data loaded. History length: {len(strategy.market_data_history)}")

        # データハンドラーをバックグラウンドで開始
        data_task = asyncio.create_task(data_handler.start())

        last_processed_timestamp = None
        while True:
            # BOT停止フラグが立っている場合はループを抜ける
            if not bot_monitor.is_bot_running():
                logger.warning("BOT停止フラグが検出されました。処理を終了します。")
                break

            # 最新市場データを取得
            market_data = data_handler.get_latest_data()
            if market_data:
                current_timestamp = pd.to_datetime(market_data.get("timestamp"))
                if current_timestamp != last_processed_timestamp:
                    if current_timestamp not in processed_timestamps:
                        logger.debug(f"Processing new market data: {market_data}")
                        strategy.update_market_data(market_data)
                        processed_timestamps.add(current_timestamp)
                        last_processed_timestamp = current_timestamp

                        # 古いタイムスタンプの整理（履歴が1000件を超えた場合）
                        if len(processed_timestamps) > 1000:
                            processed_timestamps.clear()
                            processed_timestamps.update(
                                strategy.market_data_history['timestamp'].tail(500).tolist()
                            )

                    logger.debug(f"Market data history length: {len(strategy.market_data_history)}")

                    # 戦略評価と注文実行
                    await strategy.evaluate_and_execute(order_manager)

                    # 市場価格の更新
                    if market_data.get("close"):
                        order_manager.update_market_price(float(market_data["close"]))

            # オーダー状態のチェック
            await order_manager.check_orders_status()

            # POLLING_INTERVAL秒待機（環境変数から設定）
            await asyncio.sleep(settings.settings.POLLING_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Shutting down BOT...")
    except Exception as e:
        logger.error(f"Unexpected error in main loop: {str(e)}")
        # 未処理の例外発生時に監視モジュールへアラート送信
        if 'bot_monitor' in locals():
            bot_monitor.trigger_alert("予期せぬエラー", str(e))
    finally:
        if 'data_task' in locals():
            data_task.cancel()
            try:
                await data_task
            except asyncio.CancelledError:
                pass

if __name__ == "__main__":
    asyncio.run(main())
