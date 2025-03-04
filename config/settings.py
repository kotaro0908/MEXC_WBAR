import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    UDI = os.getenv("UDI")
    API_KEY = os.getenv("API_KEY")
    API_SECRET = os.getenv("API_SECRET")
    CCXT_SYMBOL = os.getenv("CCXT_SYMBOL")
    WS_SYMBOL = os.getenv("WS_SYMBOL")
    LOT_SIZE = float(os.getenv("LOT_SIZE", 1))
    LEVERAGE = int(os.getenv("LEVERAGE", 200))
    POSITION_THRESHOLD = float(os.getenv("POSITION_THRESHOLD", 0.95))

    # 新しい戦略パラメーター：連続ローソク足の本数
    CONSECUTIVE_CANDLES = int(os.getenv("CONSECUTIVE_CANDLES", 2))

    # TP/SLの設定
    TP_AMOUNT = float(os.getenv("TP_AMOUNT", 0.00003))
    SL_AMOUNT = float(os.getenv("SL_AMOUNT", 0.02))
    ORDER_TIMEOUT_SEC = int(os.getenv("ORDER_TIMEOUT_SEC", 300))

    # システム設定
    POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", 1))  # デフォルトを1秒に変更
    TIMEOUT = int(os.getenv("TIMEOUT", 10))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    ENVIRONMENT = os.getenv("ENVIRONMENT", "production")
    DEBUG_MODE = os.getenv("DEBUG_MODE", "OFF")

    # マーチンゲール永続化設定
    PERSISTENCE_ENABLED = os.getenv("PERSISTENCE_ENABLED", "TRUE").upper() == "TRUE"
    TRADE_STATE_FILE = os.getenv("TRADE_STATE_FILE", "trade_state.json")
    MARTINGALE_RESET_TIMEOUT = int(os.getenv("MARTINGALE_RESET_TIMEOUT", 0))


settings = Settings()