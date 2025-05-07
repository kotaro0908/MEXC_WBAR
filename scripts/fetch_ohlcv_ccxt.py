#!/usr/bin/env python3
import requests
import time
import datetime as dt
import csv
from pathlib import Path
import json
import os

# 設定
SYMBOL = "SOL_USDT"  # 先物市場のシンボル形式（アンダースコア区切り）
INTERVAL = "Min1"  # 1分足 (先物市場形式：Min1、Min5、Min15、Min30、Min60、Hour4、Hour8、Day1、Week1、Month1)
TOTAL_DAYS = 90  # 合計で90日分のデータを取得
PERIOD_DAYS = 30  # 30日ごとに分けて取得

# 指定のパス
OUTPUT_DIR = Path("C:/Users/Administrator/Desktop/MEXC_WBAR/src/data")
OUTPUT_FILE = OUTPUT_DIR / "ohlcv_1m.csv"

# MEXC先物APIのエンドポイント
BASE_URL = "https://contract.mexc.com"
KLINE_ENDPOINT = f"/api/v1/contract/kline/{SYMBOL}"  # 正確なAPIエンドポイント


def fetch_klines(interval, start=None, end=None):
    """MEXC先物APIからローソク足データを取得する関数"""
    url = f"{BASE_URL}{KLINE_ENDPOINT}"

    params = {
        "interval": interval
    }

    if start:
        params["start"] = int(start / 1000)  # ミリ秒→秒に変換

    if end:
        params["end"] = int(end / 1000)  # ミリ秒→秒に変換

    try:
        print(f"APIリクエスト: {url}")
        print(f"パラメータ: {params}")

        response = requests.get(url, params=params, timeout=10)

        print(f"HTTPステータスコード: {response.status_code}")
        print(f"レスポンス（先頭100文字）: {response.text[:100] if len(response.text) > 100 else response.text}")

        response.raise_for_status()  # エラーがあれば例外を発生

        data = response.json()

        if data.get("success") and "data" in data:
            # APIレスポンス構造に基づいてデータを抽出
            result_data = data["data"]

            # APIレスポンスの構造に合わせて処理
            time_array = result_data.get("time", [])
            open_array = result_data.get("open", [])
            high_array = result_data.get("high", [])
            low_array = result_data.get("low", [])
            close_array = result_data.get("close", [])
            vol_array = result_data.get("vol", [])

            print(f"取得した時間データ数: {len(time_array)}")

            # データがない場合はすぐに戻る
            if len(time_array) == 0:
                return []

            # 配列の長さを確認
            min_length = min(len(time_array), len(open_array), len(high_array),
                             len(low_array), len(close_array), len(vol_array))

            result = []
            for i in range(min_length):
                kline = [
                    time_array[i] * 1000,  # 秒→ミリ秒に戻す
                    float(open_array[i]),
                    float(high_array[i]),
                    float(low_array[i]),
                    float(close_array[i]),
                    float(vol_array[i])
                ]
                result.append(kline)

            return result
        else:
            print(f"APIエラーまたは予期しないレスポンス形式: {data}")
            return []

    except Exception as e:
        print(f"例外が発生しました: {e}")
        print(f"レスポンス: {response.text if 'response' in locals() else 'なし'}")
        return []


def fetch_data_in_batches(interval, start_time, end_time, batch_size=2 * 24 * 60 * 60 * 1000):
    """複数のバッチに分けてデータを取得する関数（2日ごと）"""
    all_klines = []
    current_start = start_time

    while current_start < end_time:
        current_end = min(current_start + batch_size, end_time)

        print(
            f"バッチデータ取得中: {dt.datetime.fromtimestamp(current_start / 1000, dt.UTC)} から {dt.datetime.fromtimestamp(current_end / 1000, dt.UTC)}")

        klines = fetch_klines(interval, current_start, current_end)

        if klines:
            all_klines.extend(klines)
            print(f"このバッチで{len(klines)}本のローソク足を取得しました")

            # 次のバッチの開始時間を設定
            if len(klines) > 0:
                # 最後のローソク足の時間 + 1分
                last_timestamp = klines[-1][0]
                current_start = last_timestamp + 60000  # 1分 = 60000ミリ秒
            else:
                # データがない場合は時間枠をスキップ
                current_start = current_end
        else:
            print(f"このバッチでデータを取得できませんでした。次のバッチに進みます。")
            current_start = current_end

        # APIレート制限を考慮して少し待機
        time.sleep(1)

    return all_klines


def fetch_data_by_periods(interval, periods):
    """複数の期間に分けてデータを取得する関数"""
    all_klines = []

    for period_start, period_end in periods:
        print(
            f"\n期間データ取得中: {dt.datetime.fromtimestamp(period_start / 1000, dt.UTC)} から {dt.datetime.fromtimestamp(period_end / 1000, dt.UTC)}")

        period_klines = fetch_data_in_batches(interval, period_start, period_end)

        if period_klines:
            all_klines.extend(period_klines)
            print(f"この期間で{len(period_klines)}本のローソク足を取得しました")
        else:
            print(f"この期間でデータを取得できませんでした。")

        # 期間間で少し待機して、APIレート制限に引っかからないようにする
        time.sleep(5)

    return all_klines


def main():
    # 現在時刻（ミリ秒）
    now = int(time.time() * 1000)

    # 期間を設定（現在の日付から逆算して90日分を3つの30日期間に分ける）
    periods = []
    for i in range(TOTAL_DAYS // PERIOD_DAYS):
        period_end = now - (i * PERIOD_DAYS * 24 * 60 * 60 * 1000)
        period_start = period_end - (PERIOD_DAYS * 24 * 60 * 60 * 1000)
        periods.append((period_start, period_end))

    # 各期間の詳細を表示
    print("取得予定の期間:")
    for i, (start, end) in enumerate(periods):
        start_date = dt.datetime.fromtimestamp(start / 1000, dt.UTC)
        end_date = dt.datetime.fromtimestamp(end / 1000, dt.UTC)
        print(f"期間{i + 1}: {start_date} から {end_date}")

    # 複数の期間でデータを取得
    klines = fetch_data_by_periods(INTERVAL, periods)

    if klines:
        # 時間順にソート
        klines.sort(key=lambda x: x[0])

        print(f"合計：{len(klines)}本のローソク足を取得しました")

        # 最初と最後のローソク足を表示
        if len(klines) > 0:
            first_kline = klines[0]
            first_time = dt.datetime.fromtimestamp(first_kline[0] / 1000, dt.UTC)
            print(
                f"最初のローソク足: 時間={first_time}, O={first_kline[1]}, H={first_kline[2]}, L={first_kline[3]}, C={first_kline[4]}, V={first_kline[5]}")

            if len(klines) > 1:
                last_kline = klines[-1]
                last_time = dt.datetime.fromtimestamp(last_kline[0] / 1000, dt.UTC)
                print(
                    f"最後のローソク足: 時間={last_time}, O={last_kline[1]}, H={last_kline[2]}, L={last_kline[3]}, C={last_kline[4]}, V={last_kline[5]}")

        # ディレクトリが存在しない場合は作成
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # データをCSVに保存
        with OUTPUT_FILE.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])

            for kline in klines:
                timestamp = kline[0]
                dt_obj = dt.datetime.fromtimestamp(timestamp / 1000, dt.UTC)
                open_price = kline[1]
                high_price = kline[2]
                low_price = kline[3]
                close_price = kline[4]
                volume = kline[5]

                writer.writerow([dt_obj, open_price, high_price, low_price, close_price, volume])

        print(f"データを指定のパスに保存しました: {OUTPUT_FILE}")
    else:
        print("データが取得できませんでした")


if __name__ == "__main__":
    main()