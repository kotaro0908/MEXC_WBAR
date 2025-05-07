#!/usr/bin/env python3
"""
CLI ラッパー : 引数をそのまま src.research.optimize に渡す
"""

import runpy
import sys

if __name__ == "__main__":
    # sys.argv[0] を疑似的にモジュールパスへ書き換え
    sys.argv[0] = "src.research.optimize"
    # そのまま実行
    runpy.run_module("src.research.optimize", run_name="__main__")
