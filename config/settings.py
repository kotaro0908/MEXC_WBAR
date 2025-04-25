# config/settings.py
import os
from pathlib import Path
from dotenv import load_dotenv

# ------------------------------------------------------------
# .env を読み込む
# ------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parents[1]   # プロジェクト直下
ENV_FILE = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_FILE)


class Settings:
    # ===== 認証情報 =====
    UDI         = os.getenv("UDI")
    API_KEY     = os.getenv("API_KEY")
    API_SECRET  = os.getenv("API_SECRET")

    # ===== シンボル & ロット =====
    CCXT_SYMBOL         = os.getenv("CCXT_SYMBOL")          # 例: "SOL/USDT:USDT"
    WS_SYMBOL           = os.getenv("WS_SYMBOL")            # 例: "SOL_USDT"
    LOT_SIZE            = float(os.getenv("LOT_SIZE", 1))
    LEVERAGE            = int(os.getenv("LEVERAGE", 200))
    POSITION_THRESHOLD  = float(os.getenv("POSITION_THRESHOLD", 0.95))

    # ===== エントリー条件 =====
    CONSECUTIVE_CANDLES = int(os.getenv("CONSECUTIVE_CANDLES", 2))
    DIRECTION_MATCH_CHECK = os.getenv("DIRECTION_MATCH_CHECK", "FALSE").upper() == "TRUE"

    # ===== TP / SL =====
    # オフセットは % 指定（RR = 1:1）。旧 TP_AMOUNT / SL_AMOUNT は使用しない
    OFFSET_PCT          = float(os.getenv("OFFSET_PCT", 0.15))

    # ===== マーチンゲール =====
    MARTIN_FACTOR       = float(os.getenv("MARTIN_FACTOR", 2))   # 倍率
    MAX_LEVEL           = int(os.getenv("MAX_LEVEL", 6))
    PERSISTENCE_ENABLED = os.getenv("PERSISTENCE_ENABLED", "TRUE").upper() == "TRUE"
    TRADE_STATE_FILE    = os.getenv("TRADE_STATE_FILE", "trade_state.json")
    MARTINGALE_RESET_TIMEOUT = int(os.getenv("MARTINGALE_RESET_TIMEOUT", 0))

    # ===== 追加フィルタ ON/OFF =====
    USE_VOL_SPIKE       = os.getenv("USE_VOL_SPIKE", "FALSE").upper() == "TRUE"
    USE_ATR_OFFSET      = os.getenv("USE_ATR_OFFSET", "FALSE").upper() == "TRUE"
    USE_DOW_BREAK       = os.getenv("USE_DOW_BREAK", "FALSE").upper() == "TRUE"

    # ===== フィルタパラメータ =====
    SPIKE_RATIO         = float(os.getenv("SPIKE_RATIO", 1.3))
    ATR_RATIO_MIN       = float(os.getenv("ATR_RATIO_MIN", 0.7))
    ATR_RATIO_MAX       = float(os.getenv("ATR_RATIO_MAX", 1.5))
    BREAK_WINDOW        = int(os.getenv("BREAK_WINDOW", 4))
    TIMEOUT_MIN         = int(os.getenv("TIMEOUT_MIN", 15))

    # ===== 注文管理 =====
    ORDER_TIMEOUT_SEC   = int(os.getenv("ORDER_TIMEOUT_SEC", 60))

    # ===== システム設定 =====
    POLLING_INTERVAL    = int(os.getenv("POLLING_INTERVAL", 5))
    TIMEOUT             = int(os.getenv("TIMEOUT", 10))
    LOG_LEVEL           = os.getenv("LOG_LEVEL", "INFO")
    ENVIRONMENT         = os.getenv("ENVIRONMENT", "production")
    DEBUG_MODE          = os.getenv("DEBUG_MODE", "OFF")

    # ===== BOX 相場（レンジ）検出 =====
    MA_PERIOD           = int(os.getenv("MA_PERIOD", 20))
    SLOPE_PERIOD        = int(os.getenv("SLOPE_PERIOD", 10))
    SLOPE_THRESHOLD     = float(os.getenv("SLOPE_THRESHOLD", 0.3))

    # ===== 監視 & 通知 =====
    MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", 10))

    DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

    EMAIL_NOTIFICATIONS = os.getenv("EMAIL_NOTIFICATIONS", "FALSE").upper() == "TRUE"
    EMAIL_FROM          = os.getenv("EMAIL_FROM")
    EMAIL_TO            = os.getenv("EMAIL_TO")
    EMAIL_SMTP_SERVER   = os.getenv("EMAIL_SMTP_SERVER")
    EMAIL_SMTP_PORT     = int(os.getenv("EMAIL_SMTP_PORT", 587))
    EMAIL_USERNAME      = os.getenv("EMAIL_USERNAME")
    EMAIL_PASSWORD      = os.getenv("EMAIL_PASSWORD")

    TWILIO_ENABLED      = os.getenv("TWILIO_ENABLED", "FALSE").upper() == "TRUE"
    TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_FROM_NUMBER  = os.getenv("TWILIO_FROM_NUMBER")
    TWILIO_TO_NUMBER    = os.getenv("TWILIO_TO_NUMBER")
    TWILIO_MAX_ATTEMPTS = int(os.getenv("TWILIO_MAX_ATTEMPTS", 5))
    TWILIO_CALL_INTERVAL= int(os.getenv("TWILIO_CALL_INTERVAL", 60))

    CALL_ON_ERROR       = os.getenv("CALL_ON_ERROR", "FALSE").upper() == "TRUE"
    CALL_ON_INACTIVITY  = os.getenv("CALL_ON_INACTIVITY", "FALSE").upper() == "TRUE"
    CALL_ON_BOT_STOP    = os.getenv("CALL_ON_BOT_STOP", "FALSE").upper() == "TRUE"

    INACTIVITY_MONITOR_ENABLED = os.getenv("INACTIVITY_MONITOR_ENABLED", "FALSE").upper() == "TRUE"
    INACTIVITY_TIMEOUT_MINUTES = int(os.getenv("INACTIVITY_TIMEOUT_MINUTES", 60))
    INACTIVITY_CHECK_INTERVAL  = int(os.getenv("INACTIVITY_CHECK_INTERVAL", 300))

    MONITOR_TEST_MODE   = os.getenv("MONITOR_TEST_MODE", "FALSE").upper() == "TRUE"


settings = Settings()
