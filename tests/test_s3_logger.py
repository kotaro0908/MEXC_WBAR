#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
S3ロギング機能のテストスクリプト
読み込みと書き込みが正しく動作するか確認します
"""

import os
import sys
import json
import logging
import datetime
from dotenv import load_dotenv

# バージョン互換性を持つUTC時間取得関数
if sys.version_info >= (3, 9):
    # Python 3.9以降は datetime.UTC が使用可能
    def get_utc_now():
        return datetime.datetime.now(datetime.UTC)
else:
    # 古いバージョンでは datetime.timezone.utc を使用
    def get_utc_now():
        return datetime.datetime.now(datetime.timezone.utc)

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# カレントディレクトリをプロジェクトルートに設定
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
sys.path.insert(0, project_root)

# .envファイルの読み込み
load_dotenv()

# 設定の読み込み
try:
    from config.s3_settings import (
        S3_ENABLED, S3_BUCKET_NAME, S3_REGION,
        AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    )
    from utils.s3_logger import S3Logger
except ImportError as e:
    logger.error(f"必要なモジュールのインポートに失敗しました: {e}")
    sys.exit(1)


def test_s3_config():
    """S3の設定が正しく読み込まれているか確認"""
    logger.info("=== S3設定確認 ===")
    logger.info(f"S3有効: {S3_ENABLED}")
    logger.info(f"S3バケット: {S3_BUCKET_NAME}")
    logger.info(f"S3リージョン: {S3_REGION}")
    logger.info(f"AWS Access Key ID: {AWS_ACCESS_KEY_ID[:4]}...{AWS_ACCESS_KEY_ID[-4:] if AWS_ACCESS_KEY_ID else ''}")
    logger.info(f"AWS Secret Access Key: {'*' * 8}")

    if not S3_ENABLED:
        logger.warning("S3ロギングが無効です。S3_ENABLED=TRUE を設定してください。")
    if not S3_BUCKET_NAME:
        logger.error("S3_BUCKET_NAME が設定されていません。")
    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        logger.error("AWS認証情報が設定されていません。")


def test_s3_write_read():
    """S3への書き込みと読み込みをテスト"""
    if not S3_ENABLED:
        logger.error("S3ロギングが無効です。テストをスキップします。")
        return False

    logger.info("=== S3書き込み/読み込みテスト ===")

    # テスト用のシンボルと識別子
    test_symbol = "TEST_SYMBOL"
    test_id = get_utc_now().strftime("%Y%m%d%H%M%S")

    # S3Loggerインスタンスの作成
    s3_logger = S3Logger(
        bucket_name=S3_BUCKET_NAME,
        symbol=test_symbol,
        bot_name=f"test_bot_{test_id}",
        region_name=S3_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY
    )

    # バッファサイズを1に設定（即時アップロード）
    s3_logger.max_buffer_size = 1

    # テストデータ
    test_trade_data = {
        "test_id": test_id,
        "trade_id": f"T{test_id}",
        "direction": "LONG",
        "entry_price": 100.0,
        "exit_price": 105.0,
        "pnl": 5.0,
        "timestamp": get_utc_now().isoformat()
    }

    # トレードログの書き込み
    logger.info("トレードログの書き込みをテストします...")
    write_success = s3_logger.log_trade(test_trade_data)
    if not write_success:
        logger.error("トレードログの書き込みに失敗しました。")
        return False

    # 明示的にフラッシュして確実にアップロード
    flush_success = s3_logger.flush()
    if not flush_success:
        logger.error("ログバッファのフラッシュに失敗しました。")
        return False

    logger.info("トレードログの書き込みに成功しました。")

    # S3から読み込み
    logger.info("S3からの読み込みをテストします...")
    s3_data = s3_logger._get_existing_data("trades")

    if s3_data is None:
        logger.error("S3からのデータ読み込みに失敗しました。")
        return False

    # データ検証
    if isinstance(s3_data, list) and len(s3_data) > 0:
        found = False
        for item in s3_data:
            if item.get("test_id") == test_id:
                found = True
                logger.info("テストデータがS3で見つかりました。書き込み/読み込みテスト成功!")
                logger.info(f"取得データ: {json.dumps(item, indent=2)}")
                break

        if not found:
            logger.error("テストデータがS3で見つかりませんでした。")
            return False
    else:
        logger.error(f"予期しないデータ形式: {type(s3_data)}")
        return False

    return True


def test_log_performance():
    """パフォーマンスログのテスト"""
    if not S3_ENABLED:
        logger.error("S3ロギングが無効です。テストをスキップします。")
        return False

    logger.info("=== パフォーマンスログテスト ===")

    # テスト用のシンボルと識別子
    test_symbol = "TEST_SYMBOL"
    test_id = get_utc_now().strftime("%Y%m%d%H%M%S")

    # S3Loggerインスタンスの作成
    s3_logger = S3Logger(
        bucket_name=S3_BUCKET_NAME,
        symbol=test_symbol,
        bot_name=f"test_bot_{test_id}",
        region_name=S3_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY
    )

    # テストデータ
    performance_data = {
        "test_id": test_id,
        "total_trades": 10,
        "win_rate": 60.0,
        "profit_loss": 150.0,
        "max_drawdown": 50.0,
        "timestamp": get_utc_now().isoformat()
    }

    # パフォーマンスデータの書き込み
    logger.info("パフォーマンスデータの書き込みをテストします...")
    write_success = s3_logger.log_performance(performance_data)
    if not write_success:
        logger.error("パフォーマンスデータの書き込みに失敗しました。")
        return False

    logger.info("パフォーマンスデータの書き込みに成功しました。")

    # S3から読み込み
    logger.info("S3からの読み込みをテストします...")
    s3_data = s3_logger._get_existing_data("performance")

    if s3_data is None:
        logger.error("S3からのデータ読み込みに失敗しました。")
        return False

    # データ検証
    if isinstance(s3_data, dict) and s3_data.get("test_id") == test_id:
        logger.info("パフォーマンステストデータがS3で見つかりました。テスト成功!")
        logger.info(f"取得データ: {json.dumps(s3_data, indent=2)}")
        return True
    else:
        logger.error(f"予期しないデータ形式または一致しないデータ: {s3_data}")
        return False


def main():
    """メイン関数"""
    logger.info("S3ロギング機能のテストを開始します...")

    # S3設定確認
    test_s3_config()

    # S3書き込み/読み込みテスト
    if test_s3_write_read():
        logger.info("トレードログの書き込み/読み込みテストに成功しました。")
    else:
        logger.error("トレードログの書き込み/読み込みテストに失敗しました。")

    # パフォーマンスログテスト
    if test_log_performance():
        logger.info("パフォーマンスログテストに成功しました。")
    else:
        logger.error("パフォーマンスログテストに失敗しました。")

    logger.info("S3ロギング機能のテストが完了しました。")


if __name__ == "__main__":
    main()