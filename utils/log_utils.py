# utils/log_utils.py

import json
import logging
import os
import sys
import datetime
from typing import Dict, Any, Optional

# バージョン互換性を持つUTC時間取得関数
if sys.version_info >= (3, 9):
    # Python 3.9以上ではより簡潔な書き方が可能
    def get_utc_now():
        return datetime.datetime.now(datetime.timezone.utc)
else:
    # Python 3.8以下の互換性のための実装
    def get_utc_now():
        return datetime.datetime.now(datetime.timezone.utc)

# 設定ファイルをインポート
from config import settings

try:
    from config.s3_settings import S3_ENABLED, S3_BUCKET_NAME, S3_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, \
        S3_LOG_BUFFER_SIZE

    s3_config_available = True
except ImportError:
    # S3設定ファイルがない場合はデフォルト値を使用
    S3_ENABLED = False
    S3_BUCKET_NAME = ""
    S3_REGION = ""
    AWS_ACCESS_KEY_ID = ""
    AWS_SECRET_ACCESS_KEY = ""
    S3_LOG_BUFFER_SIZE = 10
    s3_config_available = False

# S3Loggerをインポート（S3有効時のみ）
s3_logger = None
if S3_ENABLED and s3_config_available:
    try:
        from utils.s3_logger import S3Logger

        s3_logger = S3Logger(
            bucket_name=S3_BUCKET_NAME,
            symbol=settings.settings.CCXT_SYMBOL,
            region_name=S3_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY
        )
        # バッファサイズを設定ファイルから設定
        if hasattr(s3_logger, 'max_buffer_size') and S3_LOG_BUFFER_SIZE:
            s3_logger.max_buffer_size = S3_LOG_BUFFER_SIZE
        logging.info("S3Logger initialized successfully")
    except ImportError as e:
        logging.error(f"S3Logger import failed: {e}")
    except Exception as e:
        logging.error(f"S3Logger initialization failed: {e}")

# ロガーの設定
logger = logging.getLogger(__name__)


def log_json(event_type: str, data: Dict[str, Any]) -> None:
    """
    構造化ログをJSONフォーマットで出力し、S3にも送信する

    Args:
        event_type: イベントタイプ
        data: ログデータ
    """
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)

    # ローカルのログファイル名
    today = get_utc_now().strftime('%Y%m%d')
    log_file = os.path.join(log_dir, f"event_log_{today}.jsonl")

    # タイムスタンプとイベントタイプを追加
    log_data = {
        "timestamp": get_utc_now().isoformat(),
        "event_type": event_type,
        **data
    }

    # ローカルログファイルに追記
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(json.dumps(log_data) + '\n')

    # ログ出力
    logger.info(f"Event logged: {event_type}")

    # S3にもログを送信（S3有効時のみ）
    if s3_logger is not None:
        try:
            # イベントタイプに応じて適切なログ関数を呼び出す
            if event_type in ["ENTRY_FILLED", "ORDER_PLACED"]:
                s3_logger.log_entry_signal(log_data)
            elif event_type in ["TP_ORDER_FILLED", "SL_ORDER_FILLED", "ORDER_CANCELED"]:
                s3_logger.log_trade(log_data)
            # その他のイベントタイプは共通のトレードログに記録
            else:
                s3_logger.log_trade(log_data)
        except Exception as e:
            logger.error(f"Failed to log to S3: {e}")


def log_trade_result(trade_result: Dict[str, Any]) -> None:
    """
    取引結果をログに記録し、パフォーマンス集計に追加する

    Args:
        trade_result: 取引結果データ
    """
    # イベントタイプを追加
    event_type = "TRADE_RESULT"
    log_data = {
        "timestamp": get_utc_now().isoformat(),
        "event_type": event_type,
        **trade_result
    }

    # JSONログに記録
    log_json(event_type, trade_result)

    # S3にもパフォーマンスデータとして記録（S3有効時のみ）
    if s3_logger is not None:
        try:
            # トレード結果を記録
            s3_logger.log_trade(log_data)

            # 日次パフォーマンスに集計
            # 単純な実装として、最新のトレード結果を含むパフォーマンスサマリーを作成
            performance_data = {
                "latest_trade": trade_result,
                "timestamp": get_utc_now().isoformat()
            }

            # 損益や勝率などの統計を計算する場合はここで追加

            # パフォーマンスデータをS3に記録
            s3_logger.log_performance(performance_data)

            # トレード結果記録後にS3バッファを即座にフラッシュ
            flush_success = s3_logger.flush()
            if flush_success:
                logger.info("S3 log buffer flushed successfully after trade result")
            else:
                logger.warning("Failed to flush S3 log buffer after trade result")
        except Exception as e:
            logger.error(f"Failed to log trade result to S3: {e}")


def flush_s3_logs() -> None:
    """
    S3ログバッファをフラッシュする
    """
    if s3_logger is not None:
        try:
            success = s3_logger.flush()
            if success:
                logger.info("S3 log buffer flushed successfully")
            else:
                logger.warning("Failed to flush S3 log buffer")
        except Exception as e:
            logger.error(f"Error when flushing S3 logs: {e}")