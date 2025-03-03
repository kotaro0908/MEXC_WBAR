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
    BB_PERIOD = int(os.getenv("BB_PERIOD", 20))
    BB_STD_DEV = float(os.getenv("BB_STD_DEV", 2.0))
    ENTRY_PRICE_OFFSET = float(os.getenv("ENTRY_PRICE_OFFSET", 0.00001))
    TRIGGER_SIGMA_THRESHOLD = float(os.getenv("TRIGGER_SIGMA_THRESHOLD", 1.5))
    ADX_THRESHOLD = float(os.getenv("ADX_THRESHOLD", 20))  # ADXの上限閾値
    ADX_LOWER_THRESHOLD = float(os.getenv("ADX_LOWER_THRESHOLD", 10))  # ADXの下限閾値を追加
    ADX_PERIOD = int(os.getenv("ADX_PERIOD", 14))  # ADXの参照期間
    TP_AMOUNT = float(os.getenv("TP_AMOUNT", 0.00003))
    SL_AMOUNT = float(os.getenv("SL_AMOUNT", 0.02))
    ORDER_TIMEOUT_SEC = int(os.getenv("ORDER_TIMEOUT_SEC", 300))
    POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", 1))
    TIMEOUT = int(os.getenv("TIMEOUT", 10))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    ENVIRONMENT = os.getenv("ENVIRONMENT", "production")
    DEBUG_MODE = os.getenv("DEBUG_MODE", "OFF")

    # マーチンゲール永続化設定（新規追加）
    PERSISTENCE_ENABLED = os.getenv("PERSISTENCE_ENABLED", "TRUE").upper() == "TRUE"
    TRADE_STATE_FILE = os.getenv("TRADE_STATE_FILE", "trade_state.json")
    # マーチンゲール状態リセットまでの無取引時間（秒）（0=リセットしない）
    MARTINGALE_RESET_TIMEOUT = int(os.getenv("MARTINGALE_RESET_TIMEOUT", 0))


settings = Settings()