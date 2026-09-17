import os
import sys

# Tambahkan root direktori ke sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings

class MockSymbolInfo:
    def __init__(self, symbol="XAUUSD", point=0.01, trade_contract_size=100.0, trade_tick_value=1.0, volume_min=0.01, volume_max=100.0, volume_step=0.01):
        self.name = symbol
        self.point = point
        self.trade_contract_size = trade_contract_size
        self.trade_tick_value = trade_tick_value
        self.volume_min = volume_min
        self.volume_max = volume_max
        self.volume_step = volume_step

def test_nominal_risk_calculation():
    xau_info = MockSymbolInfo(symbol="XAUUSD", trade_contract_size=100.0)
    sl_dist = 2.50
    lot_size = 0.01
    loss_usd = sl_dist * lot_size * xau_info.trade_contract_size
    loss_idr = loss_usd * settings.KURS_USD_IDR
    
    assert loss_usd == 2.50, f"Expected 2.50 USD, got {loss_usd}"
    assert loss_idr == 2.50 * settings.KURS_USD_IDR, f"Expected {2.50 * settings.KURS_USD_IDR} IDR, got {loss_idr}"
    print("[PASS] Test Nominal Risk Calculation Passed.")

if __name__ == "__main__":
    test_nominal_risk_calculation()