# S3設定ファイル
# config/s3_settings.py

import os
from typing import Dict, Any

# S3関連設定
S3_ENABLED = os.getenv('S3_ENABLED', 'False').lower() in ('true', '1', 't')
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME', 'crypto-trading-data-prod')
S3_REGION = os.getenv('S3_REGION', 'ap-northeast-1')  # AWSリージョン
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID', '')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY', '')

# S3ロギング設定
S3_LOG_LOCAL_BACKUP = True  # S3アップロード失敗時のローカルバックアップ
S3_LOG_BUFFER_SIZE = 10  # バッファサイズ (この数のログがたまるとアップロード)


# 設定値の検証
def validate_s3_config() -> Dict[str, Any]:
    """S3設定の検証を行い、問題があれば警告を出力"""
    issues = {}

    if S3_ENABLED:
        if not S3_BUCKET_NAME:
            issues['S3_BUCKET_NAME'] = "S3バケット名が設定されていません"

        if not AWS_ACCESS_KEY_ID:
            issues['AWS_ACCESS_KEY_ID'] = "AWS Access Key IDが設定されていません"

        if not AWS_SECRET_ACCESS_KEY:
            issues['AWS_SECRET_ACCESS_KEY'] = "AWS Secret Access Keyが設定されていません"

    return issues


# スクリプト実行時に設定検証を行う
if __name__ == "__main__":
    issues = validate_s3_config()
    if issues:
        print("S3設定に問題があります:")
        for key, message in issues.items():
            print(f" - {key}: {message}")
    else:
        print("S3設定は正常です")
        print(f"S3有効: {S3_ENABLED}")
        print(f"S3バケット: {S3_BUCKET_NAME}")
        print(f"S3リージョン: {S3_REGION}")