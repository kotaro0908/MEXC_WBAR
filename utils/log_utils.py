import os
import json
from datetime import datetime, timezone, timedelta
from utils.logger import get_logger

logger = get_logger(__name__)
JST = timezone(timedelta(hours=+9))


def log_json(event_name, data: dict):
    now = datetime.now(JST)  # JSTで現在時刻を取得
    date_str = now.strftime("%Y%m%d")
    logs_dir = "../logs"
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    filename = os.path.join(logs_dir, f"trades_{date_str}.jsonl")

    # データに傾き値が含まれていれば保持、なければデフォルト値を設定
    if "slope_value" not in data and event_name in ["ENTRY_FILLED", "ORDER_PLACED"]:
        data["slope_value"] = data.get("slope_value", 0.0)

    log_data = {
        "trade_id": data.get("trade_id", f"T{now.strftime('%Y%m%d_%H%M%S')}"),
        "timestamp": now.isoformat(),
        "event": event_name,
        "data": data
    }
    line = json.dumps(log_data, ensure_ascii=False)
    logger.info(f"[LOG] {line}")
    with open(filename, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_trade_result(data: dict):
    now = datetime.now(JST)  # JSTで現在時刻を取得
    date_str = now.strftime("%Y%m%d")
    logs_dir = "../logs"
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    filename = os.path.join(logs_dir, f"trade_results_{date_str}.jsonl")
    # エントリー時間をJSTに変換（もしUTCで渡されている場合）
    entry_time = data.get("entry_time")
    if entry_time:
        try:
            entry_time_dt = datetime.fromisoformat(entry_time)
            if entry_time_dt.tzinfo is None:  # タイムゾーン情報がない場合はUTCとして扱う
                entry_time_dt = entry_time_dt.replace(tzinfo=timezone.utc)
            entry_time = entry_time_dt.astimezone(JST).isoformat()
        except Exception as e:
            logger.error(f"Error converting entry_time to JST: {e}")
    # ロットサイズ情報を追加
    log_data = {
        "timestamp": now.isoformat(),
        "trade_id": data.get("trade_id"),
        "entry_time": entry_time,
        "entry_price": data.get("entry_price"),
        "direction": data.get("direction"),  # "LONG" or "SHORT"
        "exit_type": data.get("exit_type"),  # "TP" or "SL"
        "exit_price": data.get("exit_price"),
        "pnl": data.get("pnl"),  # 価格差
        "current_lot_size": data.get("current_lot_size"),  # 現在のロットサイズ
        "next_lot_size": data.get("next_lot_size"),  # 次回のロットサイズ
        "martingale_factor": data.get("martingale_factor", 2),  # マーチンゲール倍率
        "slope_value": data.get("slope_value", 0.0)  # 傾き値を追加
    }
    line = json.dumps(log_data, ensure_ascii=False)
    logger.info(f"[TRADE RESULT] {line}")
    with open(filename, "a", encoding="utf-8") as f:
        f.write(line + "\n")