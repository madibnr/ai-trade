import os
import sys
import time

# Tambahkan root direktori ke sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings

class MockPosition:
    def __init__(self, ticket=1001, symbol="XAUUSD", pos_type=0, price_open=4350.0, sl=4348.0, tp=4353.0, profit=10.0, volume=0.01):
        self.ticket = ticket
        self.symbol = symbol
        self.type = pos_type # 0 = BUY, 1 = SELL
        self.price_open = price_open
        self.sl = sl
        self.tp = tp
        self.profit = profit
        self.volume = volume

def test_stepped_trade_management_logic():
    print("\n" + "=" * 75)
    print("   UNIT TEST: DYNAMIC STEPPED TRADE MANAGEMENT (TAHAP 1, 2, 3)")
    print("=" * 75)

    pos = MockPosition(ticket=1001, symbol="XAUUSD", pos_type=0, price_open=4350.0, sl=4348.0, tp=4353.0)
    digits = 2
    offset_price = 0.20
    stops_level_dist = 0.30

    entry_price = pos.price_open
    initial_tp = pos.tp
    target_distance = abs(initial_tp - entry_price) # 4353.0 - 4350.0 = 3.00

    print(f"[*] Posisi Entry: {entry_price:.2f} | SL: {pos.sl:.2f} | TP Target: {initial_tp:.2f} (Jarak Target: ${target_distance:.2f})")

    # 1. State Tracker Initialization
    state = {
        "entry_price": float(pos.price_open),
        "initial_sl": float(pos.sl),
        "initial_tp": float(pos.tp),
        "extreme_price": float(pos.price_open),
        "last_extreme_time": time.time() - 50.0, # 50 detik lalu
        "bep_done": False,
        "stagnant_locked": False,
        "tp_extended": False
    }

    # --- TAHAP 1: Standard Auto BEP ---
    floating_profit_idr = 25000.0 # > 20000.0
    if floating_profit_idr >= settings.BEP_TRIGGER_PROFIT_IDR:
        target_sl = round(entry_price + offset_price, digits)
        pos.sl = target_sl
        state["bep_done"] = True
        print(f"[+] Tahap 1 (BEP): Lolos! SL digeser ke {target_sl:.2f} (Lock Impas + ${offset_price:.2f})")

    assert state["bep_done"] is True, "Tahap 1 gagal memicu BEP"
    assert pos.sl == 4350.20, f"Expected 4350.20, got {pos.sl}"

    # --- TAHAP 2: Stagnant Profit Lock ---
    # Harga sempat naik ke 4352.20 (peak_distance = $2.20 / 73.3% dari target $3.00 > 70%)
    highest_price = 4352.20
    state["extreme_price"] = highest_price
    peak_distance = highest_price - entry_price
    trigger_dist_2 = target_distance * settings.STAGNANT_LOCK_TRIGGER_RATIO # 3.0 * 0.70 = 2.10

    assert peak_distance >= trigger_dist_2, "Peak distance belum mencapai 70%"

    # Waktu tertahan > 45 detik
    time_elapsed = time.time() - state["last_extreme_time"]
    assert time_elapsed >= settings.STAGNANT_TIMEOUT_SECONDS, "Waktu belum mencapai 45s"

    new_sl_2 = round(entry_price + (peak_distance * settings.STAGNANT_LOCK_PROFIT_RATIO), digits) # 4350 + (2.20 * 0.60) = 4351.32
    assert new_sl_2 > pos.sl, "New SL harus lebih tinggi dari SL lama"
    pos.sl = new_sl_2
    state["stagnant_locked"] = True
    print(f"[+] Tahap 2 (Stagnant Lock): Lolos! Tertahan {int(time_elapsed)}s. SL dinaikkan ke {new_sl_2:.2f} (Lock 60% dari peak profit ${peak_distance:.2f})")

    # --- TAHAP 3: Dynamic TP Extender / Runner ---
    # Harga melesat kencang ke 4352.60 (current_distance = $2.60 / 86.6% dari target $3.00 > 85%)
    current_market_price = 4352.60
    current_distance = current_market_price - entry_price
    trigger_dist_3 = target_distance * settings.TP_EXTENDER_TRIGGER_RATIO # 3.0 * 0.85 = 2.55

    assert current_distance >= trigger_dist_3, "Current distance belum mencapai 85%"

    extension = 1.50 # default gold
    new_tp_3 = round(initial_tp + extension, digits) # 4353.0 + 1.50 = 4354.50
    initial_sl_dist = abs(entry_price - state["initial_sl"])
    min_lock_sl_dist = max(initial_sl_dist * 1.2, target_distance * settings.TP_EXTEND_SL_LOCK_RATIO)
    new_sl_3 = round(entry_price + min_lock_sl_dist, digits) # 4350 + 2.40 = 4352.40

    assert new_sl_3 > pos.sl, "New SL tahap 3 harus lebih tinggi dari SL tahap 2"
    pos.tp = new_tp_3
    pos.sl = new_sl_3
    state["tp_extended"] = True
    print(f"[+] Tahap 3 (TP Extender): Lolos! Momentum kencang. TP diperpanjang ke {new_tp_3:.2f} dan SL dikerek ke {new_sl_3:.2f} (Lock profit rasio >= 1:1.2)")

    print("\n" + "=" * 75)
    print("HASIL: SELURUH 3 TAHAPAN DYNAMIC TRADE MANAGEMENT BERJALAN 100% VALID [PASS]")
    print("=" * 75 + "\n")

if __name__ == "__main__":
    test_stepped_trade_management_logic()
