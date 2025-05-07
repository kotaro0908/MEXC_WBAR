#!/usr/bin/env python3
"""
CSV が置いてあれば簡単に最適化を走らせるラッパー
"""

import subprocess
import sys
from pathlib import Path

CSV_PATH = Path("data") / "ohlcv_1m.csv"  # 適宜変更

cmd = [
    sys.executable,
    "-m",
    "src.research.optimize",
    "--csv",
    str(CSV_PATH),
    "--trials",
    "20",
    "--windows",
    "3",
]
subprocess.call(cmd)
