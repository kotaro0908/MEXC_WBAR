import os
import sys
import asyncio
from dotenv import load_dotenv

sys.path.append('.')  # カレントディレクトリをパスに追加

# 環境変数の読み込み
load_dotenv()

# テストモードを有効化
os.environ["MONITOR_TEST_MODE"] = "TRUE"

# カレントディレクトリをモジュールパスに追加
import logging
from src.monitor import BotMonitor

# 標準出力へのログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)


async def main():
    print("=" * 50)
    print("通知テストを開始します")
    print("=" * 50)

    # 環境変数の確認
    discord_url = os.getenv("DISCORD_WEBHOOK_URL", "")
    discord_set = "設定あり" if discord_url else "未設定"

    email_enabled = os.getenv("EMAIL_NOTIFICATIONS", "FALSE").upper() == "TRUE"
    email_from = os.getenv("EMAIL_FROM", "")
    email_to = os.getenv("EMAIL_TO", "")
    email_server = os.getenv("EMAIL_SMTP_SERVER", "")
    email_port = os.getenv("EMAIL_SMTP_PORT", "")
    email_user = os.getenv("EMAIL_USERNAME", "")
    email_pass = "********" if os.getenv("EMAIL_PASSWORD") else "未設定"

    print(f"Discord WebHook: {discord_set}")
    if discord_url:
        print(f"  URL: {discord_url[:20]}...")

    print(f"メール通知: {'有効' if email_enabled else '無効'}")
    if email_enabled:
        print(f"  From: {email_from}")
        print(f"  To: {email_to}")
        print(f"  Server: {email_server}:{email_port}")
        print(f"  Username: {email_user}")
        print(f"  Password: {email_pass}")

    # 監視モジュールの初期化（テスト通知が自動送信される）
    monitor = BotMonitor(max_consecutive_losses=10)

    # 明示的にテスト通知を送信
    print("\n手動テスト送信:")
    monitor.test_notifications()

    # 待機
    await asyncio.sleep(5)

    # アラートをトリガーしてみる
    print("\nアラートトリガーテスト:")
    monitor.trigger_alert("テスト警告", "これはテスト警告メッセージです。")

    # 少し待機して終了
    await asyncio.sleep(5)
    print("\nテスト完了")


if __name__ == "__main__":
    asyncio.run(main())