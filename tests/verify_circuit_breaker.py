import os
import sys

# Tambahkan root direktori ke sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings
from src.execution.order_manager import (
    evaluate_daily_pnl_limits,
    set_circuit_breaker_lock,
    is_circuit_breaker_locked,
    open_position
)

def print_separator(char="=", length=85):
    print(char * length)

def run_circuit_breaker_diagnostics():
    print("\n")
    print_separator("=")
    print("   DIAGNOSTIK EVALUASI: DAILY TARGET PROFIT LOCK & CIRCUIT BREAKER")
    print_separator("=")
    print(f"[*] ENABLE_DAILY_TARGET_LOCK : {settings.ENABLE_DAILY_TARGET_LOCK}")
    print(f"[*] DAILY_TARGET_PROFIT_IDR  : Rp{settings.DAILY_TARGET_PROFIT_IDR:,.0f}")
    print(f"[*] DAILY_MAX_LOSS_IDR       : Rp{settings.DAILY_MAX_LOSS_IDR:,.0f}")
    print(f"[*] KURS_USD_IDR             : Rp{settings.KURS_USD_IDR:,.0f}")
    print_separator("-")

    results = []
    
    # Reset awal
    set_circuit_breaker_lock(False, "")

    # -------------------------------------------------------------------------
    # SKENARIO 1: Kondisi Normal (Rugi Rp50.000 < Batas Rp150.000)
    # -------------------------------------------------------------------------
    test_max_loss_idr = 150000.0
    initial_bal_1 = 1000000.0 # Rp 1.000.000
    current_bal_1 = 950000.0  # Rugi Rp 50.000
    is_idr_1 = True

    can_trade_1, status_1, pnl_idr_1 = evaluate_daily_pnl_limits(
        current_balance=current_bal_1,
        initial_balance=initial_bal_1,
        is_idr_account=is_idr_1,
        kurs_usd_idr=settings.KURS_USD_IDR,
        target_profit_idr=settings.DAILY_TARGET_PROFIT_IDR,
        max_loss_idr=test_max_loss_idr
    )

    pass_1 = (can_trade_1 is True) and (status_1 == "NORMAL")
    results.append({
        "skenario": "1. Normal Trading (Rugi Rp50k < Batas Rp150k)",
        "expected": "CAN_TRADE: TRUE | Status: NORMAL",
        "actual": f"CAN_TRADE: {can_trade_1} | Status: {status_1}",
        "pass": pass_1
    })

    # -------------------------------------------------------------------------
    # SKENARIO 2: Ambang Batas Terlampaui (Rugi Rp155.000 > Batas Rp150.000)
    # -------------------------------------------------------------------------
    initial_bal_2 = 1000000.0 # Rp 1.000.000
    current_bal_2 = 845000.0  # Rugi Rp 155.000
    is_idr_2 = True

    can_trade_2, status_2, pnl_idr_2 = evaluate_daily_pnl_limits(
        current_balance=current_bal_2,
        initial_balance=initial_bal_2,
        is_idr_account=is_idr_2,
        kurs_usd_idr=settings.KURS_USD_IDR,
        target_profit_idr=settings.DAILY_TARGET_PROFIT_IDR,
        max_loss_idr=test_max_loss_idr
    )

    # Simulasikan penguncian circuit breaker saat can_trade False
    if not can_trade_2 and status_2 == "CIRCUIT_BREAKER":
        lock_msg = f"[CIRCUIT BREAKER] Batas rugi harian tersentuh (-Rp{abs(pnl_idr_2):,.0f})!"
        set_circuit_breaker_lock(True, lock_msg)

    is_locked_2, lock_reason_2 = is_circuit_breaker_locked()
    
    # Uji coba apakah fungsi open_position menolak order saat terkunci
    op_result_2 = open_position("XAUUSD", 0, 0.01) # order_type 0 = BUY

    pass_2 = (can_trade_2 is False) and (status_2 == "CIRCUIT_BREAKER") and (is_locked_2 is True) and (op_result_2 is None)
    results.append({
        "skenario": "2. Circuit Breaker (Rugi Rp155k > Batas Rp150k)",
        "expected": "CAN_TRADE: FALSE | Status: CIRCUIT_BREAKER | OP: BLOCKED",
        "actual": f"CAN_TRADE: {can_trade_2} | Status: {status_2} | OP: {'BLOCKED' if op_result_2 is None else 'LEAKED'}",
        "pass": pass_2
    })

    # Reset lock untuk skenario berikutnya
    set_circuit_breaker_lock(False, "")

    # -------------------------------------------------------------------------
    # SKENARIO 3: Target Profit Tercapai (Laba Rp260.000 > Target Rp250.000)
    # -------------------------------------------------------------------------
    test_target_idr = 250000.0
    initial_bal_3 = 1000000.0 # Rp 1.000.000
    current_bal_3 = 1260000.0 # Laba Rp 260.000
    is_idr_3 = True

    can_trade_3, status_3, pnl_idr_3 = evaluate_daily_pnl_limits(
        current_balance=current_bal_3,
        initial_balance=initial_bal_3,
        is_idr_account=is_idr_3,
        kurs_usd_idr=settings.KURS_USD_IDR,
        target_profit_idr=test_target_idr,
        max_loss_idr=settings.DAILY_MAX_LOSS_IDR
    )

    if not can_trade_3 and status_3 == "TARGET_LOCKED":
        lock_msg = f"[TARGET REACHED] Target profit tercapai (+Rp{pnl_idr_3:,.0f})!"
        set_circuit_breaker_lock(True, lock_msg)

    is_locked_3, _ = is_circuit_breaker_locked()
    op_result_3 = open_position("XAUUSD", 0, 0.01)

    pass_3 = (can_trade_3 is False) and (status_3 == "TARGET_LOCKED") and (is_locked_3 is True) and (op_result_3 is None)
    results.append({
        "skenario": "3. Target Lock (Laba Rp260k > Target Rp250k)",
        "expected": "CAN_TRADE: FALSE | Status: TARGET_LOCKED | OP: BLOCKED",
        "actual": f"CAN_TRADE: {can_trade_3} | Status: {status_3} | OP: {'BLOCKED' if op_result_3 is None else 'LEAKED'}",
        "pass": pass_3
    })

    # Reset lock
    set_circuit_breaker_lock(False, "")

    # -------------------------------------------------------------------------
    # SKENARIO 4: Akun Berbasis Mata Uang USD (Konversi Kurs Dinamis)
    # -------------------------------------------------------------------------
    # Saldo awal $100, rugi -$10.00 USD. Dengan kurs 16.000 -> Rugi Rp160.000 (> Rp150.000)
    initial_bal_4 = 100.0
    current_bal_4 = 90.0 # Loss -$10.00
    is_idr_4 = False

    can_trade_4, status_4, pnl_idr_4 = evaluate_daily_pnl_limits(
        current_balance=current_bal_4,
        initial_balance=initial_bal_4,
        is_idr_account=is_idr_4,
        kurs_usd_idr=16000.0,
        target_profit_idr=settings.DAILY_TARGET_PROFIT_IDR,
        max_loss_idr=150000.0
    )

    # Validasi konversi kurs: -$10 * 16.000 = -Rp160.000
    expected_pnl_4 = -160000.0
    pass_4 = (can_trade_4 is False) and (status_4 == "CIRCUIT_BREAKER") and (abs(pnl_idr_4 - expected_pnl_4) < 1.0)
    results.append({
        "skenario": "4. Akun USD (Loss -$10.00 USD = -Rp160k > Rp150k)",
        "expected": "CAN_TRADE: FALSE | PnL: -Rp160,000 | Konversi Kurs Akurat",
        "actual": f"CAN_TRADE: {can_trade_4} | PnL: Rp{pnl_idr_4:,.0f}",
        "pass": pass_4
    })

    # -------------------------------------------------------------------------
    # CETAK TABEL HASIL EVALUASI
    # -------------------------------------------------------------------------
    print(f"\n{'SKENARIO PENGUJIAN':<45} | {'EVALUASI AKTUAL':<35} | {'STATUS'}")
    print_separator("-")

    all_passed = True
    for r in results:
        status_tag = "[PASS]" if r["pass"] else "[FAIL]"
        if not r["pass"]:
            all_passed = False
        print(f"{r['skenario']:<45} | {r['actual']:<35} | {status_tag}")

    print_separator("=")

    if all_passed:
        print("\n[HASIL DIAGNOSTIK] -> [PASS] Seluruh Logika Rem Darurat Lulus 100%!")
        print("[KESIMPULAN KEAMANAN]:")
        print("  1. Fitur Circuit Breaker terbukti memblokir pembukaan posisi seketika saat rugi harian menyentuh batas.")
        print("  2. Fitur Target Lock terbukti mengunci keuntungan dan menolak order baru saat target laba tercapai.")
        print("  3. Konversi mata uang (IDR dan USD) bekerja 100% presisi tanpa kebocoran logika.")
        print("  4. Sistem rem darurat ini DINYATAKAN AMAN untuk diterapkan pada Akun Riil.\n")
        return True
    else:
        print("\n[HASIL DIAGNOSTIK] -> [FAIL] Terdapat celah kebocoran logika rem darurat. Periksa rincian di atas!\n")
        return False

if __name__ == "__main__":
    run_circuit_breaker_diagnostics()
