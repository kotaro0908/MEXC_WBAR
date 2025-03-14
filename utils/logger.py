import logging
import os
import sys
from datetime import datetime

class ColorFormatter(logging.Formatter):
    # ログレベルごとのANSIカラーコード設定
    COLORS = {
        'DEBUG': '\033[90m',  # グレー
        'INFO': '\033[97m',  # 白
        'WARNING': '\033[93m',  # 黄（任意）
        'ERROR': '\033[91m',  # 赤
        'CRITICAL': '\033[91m'  # 赤
    }
    RESET = '\033[0m'

    def format(self, record):
        # コンソール出力用に色を付与
        color = self.COLORS.get(record.levelname, self.RESET)
        # 元のメッセージを保存
        original_msg = record.msg
        # 色付きメッセージを設定
        record.msg = f"{color}{record.msg}{self.RESET}"
        # フォーマット適用
        formatted_msg = super().format(record)
        # 元のメッセージを復元（他のハンドラーのために）
        record.msg = original_msg
        return formatted_msg

def get_logger(name: str) -> logging.Logger:
    print(f"=== get_logger called for {name} ===")
    print(f"Current working directory: {os.getcwd()}")
    print(f"__file__ path: {os.path.abspath(__file__)}")

    logger = logging.getLogger(name)
    print(f"Logger handlers before: {logger.handlers}")

    if not logger.hasHandlers():
        print(f"Adding handlers for {name}")
        # .envのLOG_LEVELが設定されている場合、その値を使用（未設定の場合はINFO）
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        logger.setLevel(getattr(logging, log_level, logging.INFO))

        # コンソール出力用フォーマッター（色付き）
        console_formatter = ColorFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        # ファイル出力用フォーマッター（色なし）
        file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        # 標準出力(sys.stdout)にログを出力するハンドラを設定
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(console_formatter)
        logger.addHandler(ch)

        try:
            # プロジェクトのルートディレクトリを取得
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
            print(f"Project root: {project_root}")

            # 環境変数からログディレクトリを取得、設定されていない場合はデフォルトを使用
            log_dir = os.getenv("LOG_DIR", os.path.join(project_root, "logs"))
            log_dir = os.path.abspath(log_dir)  # 絶対パスに変換
            print(f"Log directory path: {log_dir}")

            # ログディレクトリの作成
            os.makedirs(log_dir, exist_ok=True)
            print(f"Log directory created or already exists: {log_dir}")

            # 日付ごとのログファイル名
            log_file = os.path.join(log_dir, f"bot_{datetime.now().strftime('%Y%m%d')}.log")
            abs_log_file = os.path.abspath(log_file)
            print(f"Log file path: {abs_log_file}")

            # ファイルハンドラーを設定
            fh = logging.FileHandler(abs_log_file, encoding='utf-8')
            fh.setFormatter(file_formatter)
            fh.setLevel(getattr(logging, log_level, logging.INFO))
            logger.addHandler(fh)
            print(f"Added file handler for {name} to {abs_log_file}")

            # デバッグモードの場合は詳細なデバッグログも別ファイルに出力
            if log_level == "DEBUG":
                debug_file = os.path.join(log_dir, f"debug_{datetime.now().strftime('%Y%m%d')}.log")
                debug_handler = logging.FileHandler(debug_file, encoding='utf-8')
                debug_handler.setFormatter(file_formatter)
                debug_handler.setLevel(logging.DEBUG)
                logger.addHandler(debug_handler)
                print(f"Added debug handler for {name} to {os.path.abspath(debug_file)}")

            logger.info(f"Logger initialized for {name} with level {log_level}")
            logger.info(f"Log file: {abs_log_file}")

            # ログファイルハンドラの確認
            print(f"Logger handlers after setup: {logger.handlers}")
            for handler in logger.handlers:
                if isinstance(handler, logging.FileHandler):
                    print(f"File handler path: {handler.baseFilename}")

        except Exception as e:
            print(f"Error setting up file handler: {str(e)}")
            import traceback
            print(traceback.format_exc())
    else:
        print(f"Logger {name} already has handlers: {logger.handlers}")

    return logger