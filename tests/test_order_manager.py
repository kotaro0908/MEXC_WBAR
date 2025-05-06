#!/usr/bin/env python3
"""
pytest -k market_order
"""

import os

import pytest

from src.core.order_manager import BUY, OrderManager, SELL

SYMBOL = os.getenv("TEST_SYMBOL", "ETH_USDT")


@pytest.fixture(scope="module")
def om():
    return OrderManager(symbol=SYMBOL)


def test_create_market_order(om):
    order_id = om.create_market_order(side=BUY, vol="0.01")
    assert order_id is None or order_id.isdigit()


def test_queue_exit_market(om):
    # ダミー entryId
    dummy_entry = "999999999999"
    om.queue_exit_market(
        entry_order_id=dummy_entry,
        tp_side=SELL,
        sl_side=SELL,
        vol="0.01",
    )
    # 内部マップに登録されたか
    assert dummy_entry in om._exit_map
