import os
import json
import sys
import boto3
import logging
import datetime
from botocore.exceptions import ClientError
from typing import Dict, Any, Optional, List, Union

# バージョン互換性を持つUTC時間取得関数
if sys.version_info >= (3, 9):
    # Python 3.9以降は datetime.UTC が使用可能
    def get_utc_now():
        return datetime.datetime.now(datetime.UTC)
else:
    # 古いバージョンでは datetime.timezone.utc を使用
    def get_utc_now():
        return datetime.datetime.now(datetime.timezone.utc)

# タイムスタンプをISO 8601形式（UTCを明示）で取得
def get_iso_timestamp():
    """UTCの現在時刻をISO 8601形式（Z付き）で返す"""
    return get_utc_now().isoformat().replace('+00:00', 'Z')

def sanitize_symbol(symbol: str) -> str:
    """
    ファイル名に使用できない文字（例：'/' や ':'）をアンダースコアに置換する
    """
    return symbol.replace('/', '_').replace(':', '_')


class S3Logger:
    """
    トレードログをS3に保存するためのクラス
    """

    def __init__(
            self,
            bucket_name: str,
            symbol: str,
            bot_name: str = "mexc_wbar",
            region_name: Optional[str] = None,
            aws_access_key_id: Optional[str] = None,
            aws_secret_access_key: Optional[str] = None,
            local_backup: bool = True,
            local_backup_dir: Optional[str] = None
    ):
        """
        S3Loggerの初期化

        Args:
            bucket_name: S3バケット名
            symbol: 通貨ペア (例: "SOL/USDT:USDT")
            bot_name: BOT名 (デフォルト: "mexc_wbar")
            region_name: AWSリージョン (未指定の場合は環境変数から)
            aws_access_key_id: AWSアクセスキー (未指定の場合は環境変数から)
            aws_secret_access_key: AWSシークレットキー (未指定の場合は環境変数から)
            local_backup: ローカルにもバックアップするかどうか
            local_backup_dir: ローカルバックアップディレクトリ (未指定の場合はlogsディレクトリ)
        """
        self.bucket_name = bucket_name
        self.symbol = symbol
        self.bot_name = bot_name
        self.local_backup = local_backup

        # ローカルバックアップディレクトリの設定
        if local_backup:
            self.local_backup_dir = local_backup_dir or os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "logs", "s3_backup"
            )
            os.makedirs(self.local_backup_dir, exist_ok=True)

        # S3クライアントの初期化
        self.s3_client = boto3.client(
            's3',
            region_name=region_name,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key
        )

        # ロガーの設定
        self.logger = logging.getLogger(__name__)

        # 本日の日付
        now = get_utc_now()
        self.today = now.strftime('%Y/%m/%d')
        self.today_filename = now.strftime('%Y%m%d')

        # データバッファ (バッチでアップロードするため)
        self.trade_buffer: List[Dict[str, Any]] = []
        self.signal_buffer: List[Dict[str, Any]] = []
        self.max_buffer_size = 10  # このサイズに達したらアップロード

    def _get_s3_prefix(self, log_type: str) -> str:
        """
        S3のプレフィックス（パス）を取得

        Args:
            log_type: ログタイプ ("trades", "entry_signals", "performance")

        Returns:
            S3のプレフィックス
        """
        # S3ではキーの中の'/'は仮想ディレクトリとして扱われるため、そのまま使用可能
        return f"bot_trades/{self.bot_name}/{self.symbol}/{self.today}/{log_type}_{self.today_filename}.json"

    def _get_local_path(self, log_type: str) -> str:
        """
        ローカルバックアップのパスを取得

        Args:
            log_type: ログタイプ

        Returns:
            ローカルファイルパス
        """
        # self.symbol内の不正な文字を置換する
        sanitized_symbol = sanitize_symbol(self.symbol)
        return os.path.join(
            self.local_backup_dir,
            f"{self.bot_name}_{sanitized_symbol}_{log_type}_{self.today_filename}.json"
        )

    def _upload_to_s3(self, data: Union[Dict[str, Any], List[Dict[str, Any]]], log_type: str) -> bool:
        """
        データをS3にアップロード

        Args:
            data: アップロードするデータ
            log_type: ログタイプ

        Returns:
            成功したかどうか
        """
        try:
            # 既存のデータをS3から取得
            existing_data = self._get_existing_data(log_type)

            # データを結合
            if isinstance(data, list):
                if existing_data:
                    if isinstance(existing_data, list):
                        combined_data = existing_data + data
                    else:
                        self.logger.error(
                            f"Existing data type mismatch for {log_type}. Expected list, got {type(existing_data)}")
                        combined_data = data
                else:
                    combined_data = data
            else:  # 辞書型の場合
                if existing_data:
                    if isinstance(existing_data, dict):
                        # performance データの場合、最新のデータで更新
                        combined_data = {**existing_data, **data}
                    else:
                        self.logger.error(
                            f"Existing data type mismatch for {log_type}. Expected dict, got {type(existing_data)}")
                        combined_data = data
                else:
                    combined_data = data

            # S3にアップロード
            s3_prefix = self._get_s3_prefix(log_type)
            self.s3_client.put_object(
                Body=json.dumps(combined_data, indent=2),
                Bucket=self.bucket_name,
                Key=s3_prefix
            )

            # ローカルにもバックアップ
            if self.local_backup:
                local_path = self._get_local_path(log_type)
                with open(local_path, 'w') as f:
                    json.dump(combined_data, f, indent=2)

            self.logger.info(f"Successfully uploaded {log_type} data to S3: {s3_prefix}")
            return True

        except ClientError as e:
            self.logger.error(f"Error uploading {log_type} data to S3: {str(e)}")

            # S3アップロードに失敗してもローカルには保存
            if self.local_backup:
                try:
                    local_path = self._get_local_path(log_type)
                    with open(local_path, 'w') as f:
                        json.dump(data, f, indent=2)
                    self.logger.info(f"Saved {log_type} data to local backup: {local_path}")
                except Exception as local_e:
                    self.logger.error(f"Failed to save {log_type} data to local backup: {str(local_e)}")

            return False

    def _get_existing_data(self, log_type: str) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
        """
        S3から既存のデータを取得

        Args:
            log_type: ログタイプ

        Returns:
            既存のデータ、なければNone
        """
        try:
            s3_prefix = self._get_s3_prefix(log_type)
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_prefix)
            return json.loads(response['Body'].read().decode('utf-8'))
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                # ファイルが存在しない場合は正常
                return None
            else:
                self.logger.error(f"Error getting existing data from S3: {str(e)}")
                return None
        except Exception as e:
            self.logger.error(f"Error parsing existing data from S3: {str(e)}")
            return None

    def log_trade(self, trade_data: Dict[str, Any]) -> bool:
        """
        トレード情報をログに記録

        Args:
            trade_data: トレード情報

        Returns:
            成功したかどうか
        """
        # タイムスタンプの追加
        if 'timestamp' not in trade_data:
            trade_data['timestamp'] = get_iso_timestamp()

        # バッファに追加
        self.trade_buffer.append(trade_data)

        # バッファサイズが閾値を超えたらアップロード
        if len(self.trade_buffer) >= self.max_buffer_size:
            success = self._upload_to_s3(self.trade_buffer, "trades")
            if success:
                self.trade_buffer = []
            return success

        return True

    def log_entry_signal(self, signal_data: Dict[str, Any]) -> bool:
        """
        エントリーシグナル情報をログに記録

        Args:
            signal_data: シグナル情報

        Returns:
            成功したかどうか
        """
        # タイムスタンプの追加
        if 'timestamp' not in signal_data:
            signal_data['timestamp'] = get_iso_timestamp()

        # バッファに追加
        self.signal_buffer.append(signal_data)

        # バッファサイズが閾値を超えたらアップロード
        if len(self.signal_buffer) >= self.max_buffer_size:
            success = self._upload_to_s3(self.signal_buffer, "entry_signals")
            if success:
                self.signal_buffer = []
            return success

        return True

    def log_performance(self, performance_data: Dict[str, Any]) -> bool:
        """
        パフォーマンス情報をログに記録 (日次サマリー)

        Args:
            performance_data: パフォーマンス情報

        Returns:
            成功したかどうか
        """
        # タイムスタンプの追加
        if 'timestamp' not in performance_data:
            performance_data['timestamp'] = get_iso_timestamp()

        # 即時アップロード (パフォーマンスデータはバッファリングしない)
        return self._upload_to_s3(performance_data, "performance")

    def flush(self) -> bool:
        """
        バッファに残っているデータを強制的にアップロード

        Returns:
            すべて成功したかどうか
        """
        success = True

        if self.trade_buffer:
            trade_success = self._upload_to_s3(self.trade_buffer, "trades")
            if trade_success:
                self.trade_buffer = []
            success = success and trade_success

        if self.signal_buffer:
            signal_success = self._upload_to_s3(self.signal_buffer, "entry_signals")
            if signal_success:
                self.signal_buffer = []
            success = success and signal_success

        return success