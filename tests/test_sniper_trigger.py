import os
import sys
import time

# Tambahkan root direktori ke sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings
import src.execution.order_manager as om

class MockTick:
    def __init__(self, bid: float, ask: float):
        self.bid = bid
        self.ask = ask
        self.time = int(time.time())

def test_sniper_trigger_suite():
    print("\n" + "=" * 75)
    print("   UNIT TEST: AI COMMANDER & PYTHON SNIPER (CONDITIONAL TICK TRIGGER)")
    print("=" * 75)

    symbol = "XAUUSD"

    # 1. Test set_trigger_plan & get_trigger_plan
    om.reset_trigger_plan()
    assert om.get_trigger_plan() is None, "Plan awal harus None"

    om.set_trigger_plan(
        symbol=symbol,
        action="PENDING_BUY",
        trigger_price=4376.20,
        sl_price=4374.20,
        tp_price=4379.20,
        confidence=0.90,
        expire_seconds=60.0
    )

    plan = om.get_trigger_plan()
    assert plan is not None, "Plan harus tersimpan di memori"
    assert plan["action"] == "BUY", "Action harus dinormalisasi ke BUY"
    assert plan["trigger_price"] == 4376.20, "Trigger price harus 4376.20"
    print("[+] Test 1: set_trigger_plan & normalization [PASS]")

    # 2. Test tick belum mencapai trigger price
    # Simulasikan tick pasar masih di bawah trigger price (Ask: 4375.50 < 4376.20)
    original_get_tick = om.get_current_tick
    original_get_metadata = om.get_symbol_metadata
    om.get_current_tick = lambda s: MockTick(bid=4375.20, ask=4375.50)
    om.get_symbol_metadata = lambda s: {
        "name": s, "digits": 2, "point": 0.01, "stops_level_dist": 0.30, "is_forex": False
    }

    fired = om.check_and_execute_trigger_plan(symbol, execution_mode="SIGNAL")
    assert not fired, "Trigger tidak boleh meledak jika harga Ask belum menyentuh trigger price"
    assert om.get_trigger_plan() is not None, "Plan harus tetap aktif di memori"
    print("[+] Test 2: Price below trigger -> No execution [PASS]")

    # 3. Test tick menyentuh/menembus trigger price (Breakout BUY)
    # Simulasikan tick pasar melonjak ke 4376.35 (Ask: 4376.35 >= 4376.20)
    om.get_current_tick = lambda s: MockTick(bid=4376.05, ask=4376.35)

    fired = om.check_and_execute_trigger_plan(symbol, execution_mode="SIGNAL")
    assert fired, "Trigger wajib meledak seketika saat harga Ask >= trigger price"
    assert om.get_trigger_plan() is None, "Plan harus otomatis di-reset pasca eksekusi (Anti-Double Entry)"
    print("[+] Test 3: Price hits trigger -> Instant execution & plan reset [PASS]")

    # 4. Test PENDING_SELL Breakdown
    om.set_trigger_plan(
        symbol=symbol,
        action="PENDING_SELL",
        trigger_price=4370.00,
        sl_price=4372.00,
        tp_price=4367.00,
        confidence=0.88,
        expire_seconds=60.0
    )

    # Harga Bid masih di atas 4370.00 -> Tidak boleh fired
    om.get_current_tick = lambda s: MockTick(bid=4370.50, ask=4370.80)
    fired_sell_no = om.check_and_execute_trigger_plan(symbol, execution_mode="SIGNAL")
    assert not fired_sell_no, "Trigger SELL tidak boleh meledak jika Bid masih > trigger_price"

    # Harga Bid tembus ke 4369.90 -> Fired!
    om.get_current_tick = lambda s: MockTick(bid=4369.90, ask=4370.20)
    fired_sell_yes = om.check_and_execute_trigger_plan(symbol, execution_mode="SIGNAL")
    assert fired_sell_yes, "Trigger SELL wajib meledak saat Bid <= trigger_price"
    assert om.get_trigger_plan() is None, "Plan SELL harus otomatis di-reset"
    print("[+] Test 4: PENDING_SELL breakdown execution [PASS]")

    # 5. Test Expiration (Kedaluwarsa 60 detik)
    om.set_trigger_plan(
        symbol=symbol,
        action="PENDING_BUY",
        trigger_price=4380.00,
        sl_price=4378.00,
        tp_price=4383.00,
        confidence=0.85,
        expire_seconds=0.1 # Kedaluwarsa dalam 0.1 detik
    )
    time.sleep(0.15)
    om.get_current_tick = lambda s: MockTick(bid=4380.10, ask=4380.50) # Harga cocok tapi sudah expired
    fired_exp = om.check_and_execute_trigger_plan(symbol, execution_mode="SIGNAL")
    assert not fired_exp, "Trigger yang sudah expired tidak boleh dieksekusi"
    assert om.get_trigger_plan() is None, "Plan yang expired harus otomatis dibersihkan"
    print("[+] Test 5: Trigger Plan Expiration [PASS]")

    # Kembalikan fungsi asli
    om.get_current_tick = original_get_tick
    om.get_symbol_metadata = original_get_metadata

    print("\n" + "=" * 75)
    print("HASIL: SELURUH SKENARIO SNIPER TRIGGER LULUS 100% [PASS]")
    print("=" * 75 + "\n")

if __name__ == "__main__":
    test_sniper_trigger_suite()
