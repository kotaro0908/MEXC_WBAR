import logging
import os
import sys

class ColorFormatter(logging.Formatter):
    # ログレベルごとのANSIカラーコード設定
    COLORS = {
        'DEBUG': '\033[90m',    # グレー
        'INFO': '\033[97m',     # 白
        'WARNING': '\033[93m',  # 黄（任意）
        'ERROR': '\033[91m',    # 赤
        'CRITICAL': '\033[91m'  # 赤
    }
    RESET = '\033[0m'

    def format(self, record):
        # ログレベルに応じた色を付与
        color = self.COLORS.get(record.levelname, self.RESET)
        record.msg = f"{color}{record.msg}{self.RESET}"
        return super().format(record)

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.hasHandlers():
        # .envのLOG_LEVELが設定されている場合、その値を使用（未設定の場合はINFO）
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        logger.setLevel(getattr(logging, log_level, logging.INFO))
        formatter = ColorFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        # 標準出力(sys.stdout)にログを出力するハンドラを設定
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        logger.addHandler(ch)
    return logger
