#!/usr/bin/env python3
"""
StatsTracker
============

ポジション終了時（TP / SL 約定直後）に呼び出して、
- 総トレード数
- 勝率
- 累積 PnL
- 最大ドローダウン

をローリング集計し、必要なら Notifier へ WARN / ERROR を送信する。
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

# ------------------------  設定  ------------------------ #
LOG_DIR = Path(os.getenv("STATS_LOG_DIR", "stats"))
LOG_DIR.mkdir(exist_ok=True)

ROLLING_WINDOW_TRADES = int(os.getenv("ROLLING_WINDOW_TRADES", 200))  # 直近 n 取引で評価
WARN_WINRATE = float(os.getenv("WARN_WINRATE", 0.53))                # ↓で WARN
WARN_PF = float(os.getenv("WARN_PF", 1.05))                          # ↓で WARN

# ------------------------  ログ ------------------------ #
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class StatsTracker:
    """トレードパフォーマンスをローリングで追跡します。"""

    def __init__(self) -> None:
        self._records: List[Dict] = []

    # ------------------------  公開 API ------------------------ #

    def add_trade(self, side: str, pnl: float) -> None:
        """
        1 取引終了時に呼び出す。

        Parameters
        ----------
        side : str
            "TP" or "SL"
        pnl : float
            トレードの損益（USDT）
        """
        self._records.append(
            {
                "timestamp": _dt.datetime.utcnow().isoformat(),
                "side": side,
                "pnl": pnl,
            }
        )

        # ローリング窓を維持
        if len(self._records) > ROLLING_WINDOW_TRADES:
            self._records.pop(0)

        self._save_json()
        self._check_warn()

    # --------------------  内部処理 -------------------- #
    def _save_json(self) -> None:
        with open(LOG_DIR / "stats.json", "w", encoding="utf-8") as fp:
            json.dump(self._records, fp, ensure_ascii=False, indent=2)

    def _check_warn(self) -> None:
        """勝率・PF が閾値を下回ったら WARN"""

        df = pd.DataFrame(self._records)
        if df.empty:
            return

        wins = (df["pnl"] > 0).sum()
        total = len(df)
        winrate = wins / total
        gross_profit = df.loc[df["pnl"] > 0, "pnl"].sum()
        gross_loss = -df.loc[df["pnl"] < 0, "pnl"].sum()
        pf = gross_profit / gross_loss if gross_loss else float("inf")

        logger.info(
            f"[Stats] Trades={total} WinRate={winrate:.2%} PF={pf:.2f}"
        )

        if winrate < WARN_WINRATE or pf < WARN_PF:
            logger.warning(
                f"[WARN] Performance deteriorated: WinRate {winrate:.2%}, PF {pf:.2f}"
            )
            # === Notifier 連携ポイント（必要なら実装）=== #
