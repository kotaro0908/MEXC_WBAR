import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

# project modules
from config.settings import settings
from src.strategy import Strategy
from src.order_manager import OrderManager
from src.order_monitor import OrderMonitor

################################################################################
# Fake exchange & helpers
################################################################################

class FakeOrder:
    def __init__(self, oid, price, amount, side):
        self.id = oid
        self.price = price
        self.amount = amount
        self.side = side
        self.filled = 0.0
        self.average = 0.0
        self.status = "open"

    def fill(self, price: float):
        self.filled = self.amount
        self.average = price
        self.status = "closed"


class FakeExchange:
    """Very small stub mimicking ccxt methods used in tests"""

    def __init__(self):
        self.orders = {}
        self._oid = 0
        self._markets = [
            {
                "symbol": "SOL/USDT:USDT",
                "precision": {"price": 0.01},
                "limits": {"amount": {"min": 0.001}},
            }
        ]

    # ccxt compat
    def fetch_markets(self):
        return self._markets

    def fetch_positions(self, symbols):
        return []

    def fetch_order(self, oid, symbol):
        o = self.orders[int(oid)]
        return {
            "id": o.id,
            "status": o.status,
            "amount": o.amount,
            "filled": o.filled,
            "average": o.average,
            "price": o.price,
        }

    # helpers for test
    def create_order(self, price, amount, side):
        self._oid += 1
        o = FakeOrder(self._oid, price, amount, side)
        self.orders[self._oid] = o
        return self._oid

    def fill_order(self, oid, price):
        self.orders[oid].fill(price)

################################################################################
# pytest fixture – env
################################################################################

@pytest_asyncio.fixture
async def env(monkeypatch):
    """Return namespace with strategy / order manager wired to FakeExchange"""

    fake_ex = FakeExchange()

    # patch ccxt.mexc to return fake exchange
    monkeypatch.setattr("src.order_manager.ccxt.mexc", lambda *a, **k: fake_ex)

    # patch OrderManager._place_order to create + (optionally) fill orders in fake_ex
    def fake_place_order(self, params):
        side = params["side"]
        amount = float(params["vol"])
        price = float(params.get("price", 0)) or 0.0
        oid = fake_ex.create_order(price, amount, side)
        # market order (type == "6") は即 fill にする
        if params.get("type") == "6":
            fill_price = price or 100.0
            fake_ex.fill_order(oid, fill_price)
        return {"success": True, "data": oid}

    monkeypatch.setattr(OrderManager, "_place_order", fake_place_order, raising=True)

    strategy = Strategy()
    om = OrderManager(
        trade_logic=strategy,
        ccxt_symbol="SOL/USDT:USDT",
        ws_symbol="SOL_USDT",
        lot_size=2,
        leverage=10,
        uid="DUMMY",
        api_key="KEY",
        api_secret="SECRET",
    )
    monitor = OrderMonitor(om)

    return SimpleNamespace(strategy=strategy, om=om, ex=fake_ex, mon=monitor)

################################################################################
# tests
################################################################################

@pytest.mark.asyncio
async def test_tp_flow(env):
    """after LONG entry & TP fill, martingale should reset"""
    om, ex, mon = env.om, env.ex, env.mon

    await om.place_entry_order("LONG", 100.0)
    assert om.entry_order_id is not None
    # entry already filled by fake_place_order

    await mon.run()               # detects filled entry & sets TP
    assert om.tp_order_ids
    tp_oid = next(iter(om.tp_order_ids.values()))

    ex.fill_order(tp_oid, 100.15)  # TP hit
    await mon.run()

    assert om.consec_losses == 0
    assert om.dynamic_lot == om.base_lot


@pytest.mark.asyncio
async def test_sl_martingale(env):
    """after SL, lot size doubles and consec_losses increments"""
    om, ex, mon = env.om, env.ex, env.mon

    await om.place_entry_order("SHORT", 100.0)
    await mon.run()  # entry already filled

    # inject dummy SL order and fill it
    sl_oid = ex.create_order(100.15, om.dynamic_lot, side=1)
    om.sl_order_ids[om.dynamic_lot] = sl_oid

    ex.fill_order(sl_oid, 100.15)
    await mon.run()

    assert om.consec_losses == 1
    assert om.dynamic_lot == om.base_lot * settings.MARTIN_FACTOR
