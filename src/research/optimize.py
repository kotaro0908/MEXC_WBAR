#!/usr/bin/env python3
"""
Optuna × Walk-Forward 最適化
---------------------------
使い方:
$ python -m research.optimize --csv data_1m.csv --trials 20 --windows 3

CSV は datetime,index 付きで 'open,high,low,close,volume' 列を想定。
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import optuna
import pandas as pd

from .backtest_engine import run_backtest

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def make_splits(df: pd.DataFrame, window_days: int, windows: int):
    out = []
    step = window_days * 1440
    for i in range(windows):
        start = -step * (windows - i)
        mid = start + step // 2
        train = df.iloc[start:mid]
        test = df.iloc[mid : mid + step // 2]
        out.append((train, test))
    return out


def build_objective(train_df, test_df):
    def _objective(trial: optuna.trial.Trial):
        params = {
            "SPIKE_RATIO": trial.suggest_float("spike_ratio", 1.1, 2.0),
            "USE_ATR_FILTER": trial.suggest_int("use_atr", 0, 1),
            "OFFSET_PCT": trial.suggest_float("offset_pct", 0.1, 0.3),
        }
        if params["USE_ATR_FILTER"]:
            params["ATR_RATIO_MIN"] = trial.suggest_float("atr_min", 0.4, 1.5)
            params["ATR_RATIO_MAX"] = trial.suggest_float("atr_max", 1.6, 3.0)

        pf, win_rate = run_backtest(train_df, test_df, params)
        # 目的関数：PF高 & 勝率条件達成を最小化
        if win_rate < 0.53:
            return 1e2
        return 1 / pf if pf != 0 else 1e1

    return _objective


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="1m OHLCV CSV path")
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--windows", type=int, default=3)
    parser.add_argument("--window_days", type=int, default=30)
    args = parser.parse_args()

    df = pd.read_csv(args.csv, parse_dates=["datetime"], index_col="datetime")

    best_params_all = []

    for idx, (train_df, test_df) in enumerate(
        make_splits(df, args.window_days, args.windows), 1
    ):
        study = optuna.create_study(direction="minimize")
        study.optimize(
            build_objective(train_df, test_df), n_trials=args.trials, show_progress_bar=True
        )
        best_params = study.best_trial.params
        best_params_all.append(best_params)
        (Path("output") / f"best_params_window{idx}.json").write_text(
            json.dumps(best_params, indent=2)
        )
        logger.info(f"[Window {idx}] Best params: {best_params}")

    (Path("output") / "best_params_all.json").write_text(json.dumps(best_params_all, indent=2))
    logger.info("Optimization finished.")


if __name__ == "__main__":
    main()
