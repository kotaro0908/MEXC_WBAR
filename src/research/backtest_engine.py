#!/usr/bin/env python3
"""
単純化したバックテストエンジン（ローリング 1 分足用）

DataFrame (datetime-indexed, columns: open, high, low, close, volume)
と params dict を受け取り、TP / SL ルールの結果を返す。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _generate_signals(df: pd.DataFrame, params: dict) -> pd.Series:
    """確定足 2 本連続の方向シグナル (+ 出来高/ATR フィルタ)"""
    use_atr = params.get("USE_ATR_FILTER", 0)
    spike_ratio = params["SPIKE_RATIO"]
    offset_pct = params["OFFSET_PCT"]

    price_up = (df["close"] > df["open"]).astype(int)
    price_dn = (df["close"] < df["open"]).astype(int) * -1
    direction = price_up + price_dn

    signal = (direction.shift(1) == direction) & (direction != 0)

    # volume spike
    avg_vol = df["volume"].rolling(30).mean()
    vol_ok = df["volume"] >= avg_vol * spike_ratio

    filt = signal & vol_ok

    if use_atr:
        # 簡易 ATR
        tr = np.maximum(df["high"] - df["low"],
                        np.abs(df["high"] - df["close"].shift(1)),
                        np.abs(df["low"] - df["close"].shift(1)))
        atr = pd.Series(tr).rolling(14).mean()
        atr_min = params["ATR_RATIO_MIN"]
        atr_max = params["ATR_RATIO_MAX"]
        atr_ok = (atr >= atr_min * df["close"] / 100) & (atr <= atr_max * df["close"] / 100)
        filt &= atr_ok

    return np.where(filt, direction, 0)


def run_backtest(train_df: pd.DataFrame, test_df: pd.DataFrame, params: dict):
    """
    Returns
    -------
    pf : float
    win_rate : float
    """
    sig = _generate_signals(test_df, params)
    offset = params["OFFSET_PCT"] / 100
    entries = []
    results = []

    entry_price = None
    entry_dir = 0

    for idx, row in test_df.iterrows():
        s = sig[idx]
        if entry_price is None and s != 0:
            # エントリー
            entry_price = row["close"]
            entry_dir = s
            tp = entry_price * (1 + offset * entry_dir)
            sl = entry_price * (1 - offset * entry_dir)
        elif entry_price is not None:
            # 決済判定
            if entry_dir == 1:
                if row["high"] >= tp:
                    pnl = tp - entry_price
                elif row["low"] <= sl:
                    pnl = sl - entry_price
                else:
                    continue
            else:
                if row["low"] <= tp:
                    pnl = entry_price - tp
                elif row["high"] >= sl:
                    pnl = entry_price - sl
                else:
                    continue

            entries.append(entry_price)
            results.append(pnl)
            entry_price = None
            entry_dir = 0

    if not results:
        return 0, 0

    gross_profit = sum(x for x in results if x > 0)
    gross_loss = -sum(x for x in results if x < 0)
    pf = gross_profit / gross_loss if gross_loss else float("inf")
    win_rate = sum(1 for x in results if x > 0) / len(results)
    return pf, win_rate
