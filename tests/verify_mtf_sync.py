import os
import sys
import time
from datetime import datetime

# Pastikan root direktori terdaftar dalam sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import MetaTrader5 as mt5
from config import settings
from utils.logger import logger
from src.data.mt5_connection import initialize_mt5, shutdown_mt5
from src.data.market_data import (
    get_multi_timeframe_data,
    get_symbol_info,
    get_current_tick
)
from main import is_new_candle

def print_separator(char="=", length=80):
    print(char * length)

def verify_mtf_pipeline(total_cycles=3):
    print("\n")
    print_separator("=")
    print("   DIAGNOSTIK SINKRONISASI MULTI-TIMEFRAME (M1, M5, M15) MT5")
    print_separator("=")
    
    # 1. Inisialisasi Koneksi MT5
    if not initialize_mt5():
        logger.error("[ERROR] Gagal terhubung ke terminal MT5.")
        return False

    symbol = settings.SYMBOL
    info = get_symbol_info(symbol)
    if not info:
        logger.error(f"[ERROR] Simbol {symbol} tidak valid atau tidak tersedia di broker.")
        shutdown_mt5()
        return False

    print(f"[*] Simbol Target    : {symbol} (Digits: {info.digits}, Point: {info.point})")
    print(f"[*] Total Pengujian  : {total_cycles} kali siklus pergantian lilin M1")
    print("[*] Menunggu lilin M1 pertama untuk memulai pengukuran sinkronisasi...")
    print_separator("-")

    tf_map = {
        "m1": (mt5.TIMEFRAME_M1, 60),
        "m5": (mt5.TIMEFRAME_M5, 300),
        "m15": (mt5.TIMEFRAME_M15, 900)
    }

    rates_init = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1)
    last_m1_time = rates_init[0]['time'] if rates_init is not None and len(rates_init) > 0 else 0

    completed_cycles = 0
    all_passed = True
    latencies = []
    stale_detected = {}

    try:
        while completed_cycles < total_cycles:
            # Pantau pergantian candle M1
            new_candle, current_m1_time = is_new_candle(symbol, mt5.TIMEFRAME_M1, last_m1_time)

            if new_candle:
                completed_cycles += 1
                last_m1_time = current_m1_time

                # Ukur latensi penarikan data ketiga timeframe secara presisi
                t_start = time.perf_counter()
                mtf_data = get_multi_timeframe_data(symbol)
                t_elapsed_ms = (time.perf_counter() - t_start) * 1000.0
                latencies.append(t_elapsed_ms)

                tick = get_current_tick(symbol)
                server_tick_time = tick.time if tick else int(time.time())

                print(f"\n[SIKLUS {completed_cycles}/{total_cycles}] Lilin M1 Baru Terdeteksi | Latensi MTF Fetch: {t_elapsed_ms:.2f} ms")
                print(f"Waktu Server MT5 : {datetime.fromtimestamp(server_tick_time).strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"{'TIMEFRAME':<10} | {'WAKTU BAR':<19} | {'CLOSE':<10} | {'EMA 9':<10} | {'EMA 21':<10} | {'RSI':<8} | {'ATR':<8} | {'STATUS'}")
                print_separator("-")

                # Verifikasi masing-masing timeframe
                for tf_key, (tf_const, bar_seconds) in tf_map.items():
                    data = mtf_data.get(tf_key, {})
                    payload_ts = data.get("timestamp", 0)
                    latest_time_str = data.get("latest_time", "-")
                    latest_close = data.get("latest_close", 0.0)
                    ema9 = data.get("ema9", 0.0)
                    ema21 = data.get("ema21", 0.0)
                    rsi = data.get("rsi", 0.0)
                    atr = data.get("atr", 0.0)

                    # Ambil data lilin riil langsung dari server broker sebagai ground truth
                    live_rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, 1)
                    if live_rates is not None and len(live_rates) > 0:
                        server_bar_ts = live_rates[0]['time']
                        server_close = live_rates[0]['close']
                    else:
                        server_bar_ts = 0
                        server_close = 0.0

                    # Validasi apakah payload timestamp sesuai dengan waktu lilin riil server
                    is_synced = (payload_ts == server_bar_ts) and (payload_ts > 0)
                    # Periksa apakah harga close tidak membeku / identik dengan data server
                    price_synced = abs(latest_close - server_close) < (info.point * 10)

                    if is_synced and price_synced:
                        status_str = "[SYNCED]"
                    else:
                        status_str = "[STALE]"
                        all_passed = False
                        stale_detected[tf_key] = stale_detected.get(tf_key, 0) + 1

                    print(f"{tf_key.upper():<10} | {latest_time_str:<19} | {latest_close:<10.2f} | {ema9:<10.2f} | {ema21:<10.2f} | {rsi:<8.2f} | {atr:<8.2f} | {status_str}")

                print_separator("-")
                
                if completed_cycles < total_cycles:
                    print(f"Menunggu penutupan lilin M1 berikutnya ({completed_cycles}/{total_cycles})...")

            # Sleep 1 detik untuk menghemat CPU selama polling
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[STOP] Pengujian dihentikan secara manual oleh pengguna.")
        all_passed = False

    finally:
        shutdown_mt5()

    # 3. Ringkasan Status & Evaluasi Akhir
    print("\n" + "=" * 80)
    print("   RINGKASAN EVALUASI SINKRONISASI MULTI-TIMEFRAME")
    print("=" * 80)
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
    print(f"[*] Siklus Selesai     : {completed_cycles} dari {total_cycles}")
    print(f"[*] Rata-rata Latensi  : {avg_latency:.2f} ms per penarikan (M15 + M5 + M1)")
    
    if all_passed and completed_cycles == total_cycles:
        print("\n[HASIL DIAGNOSTIK] -> [PASS] Multi-timeframe data auto-updating correctly")
        print("Data M1, M5, dan M15 terbukti selalu segar, akurat, dan tersinkronisasi 1:1 dengan server MT5.")
        return True
    else:
        stale_info = ", ".join([f"{k.upper()} ({v}x stale)" for k, v in stale_detected.items()]) if stale_detected else "Siklus tidak tuntas"
        print(f"\n[HASIL DIAGNOSTIK] -> [FAIL] Stale data detected on timeframe: {stale_info}")
        return False

if __name__ == "__main__":
    verify_mtf_pipeline(total_cycles=3)
