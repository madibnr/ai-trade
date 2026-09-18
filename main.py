import time
from datetime import datetime
import MetaTrader5 as mt5
from config import settings
from utils.logger import logger
from src.data.mt5_connection import initialize_mt5, shutdown_mt5
from src.data.market_data import (
    get_historical_data,
    is_market_open,
    get_symbol_info,
    get_current_tick
)
from src.strategy.ai_strategy import get_ai_decision
from src.execution.order_manager import (
    calculate_lot_size,
    open_position,
    close_position,
    get_active_positions,
    check_daily_drawdown_limit,
    check_and_apply_bep,
    evaluate_daily_pnl_limits,
    set_circuit_breaker_lock,
    get_daily_realized_pnl
)

TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

def is_new_candle(symbol, timeframe, last_candle_time):
    """
    Mendeteksi apakah candle baru telah terbentuk.
    Return: (boolean is_new, timestamp candle saat ini)
    """
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 1)
    if rates is None or len(rates) == 0:
        return False, last_candle_time
        
    current_time = rates[0]['time']
    if current_time > last_candle_time:
        return True, current_time
        
    return False, last_candle_time

def main():
    logger.info(f"[START] Memulai Trading Bot | Simbol: {settings.SYMBOL} | TF: {settings.TIMEFRAME_STR}")
    
    if not initialize_mt5():
        logger.error("[ERROR] Gagal terhubung ke MT5. Keluar.")
        return
        
    symbol = settings.SYMBOL
    timeframe = TF_MAP.get(settings.TIMEFRAME_STR, mt5.TIMEFRAME_M1)
    
    # Pastikan simbol terpilih dan aktif di jendela Market Watch MT5
    if not mt5.symbol_select(symbol, True):
        logger.error(f"[ERROR] Gagal memilih dan mengaktifkan simbol {symbol} di Market Watch MT5. Keluar.")
        shutdown_mt5()
        return

    info = get_symbol_info(symbol)
    if not info:
        logger.error(f"[ERROR] Gagal mendapatkan info spesifikasi {symbol}. Keluar.")
        shutdown_mt5()
        return
        
    logger.info(f"[SUCCESS] Bot Siap! Spread Saat Ini: {info.spread} point, Min Lot: {info.volume_min}")
    
    # Inisialisasi awal agar tidak langsung eksekusi di candle berjalan (belum tertutup)
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 1)
    last_candle_time = rates[0]['time'] if rates is not None and len(rates) > 0 else 0
    logger.info("[INFO] Menunggu penutupan candle pertama untuk memulai analisa...")

    # Inisialisasi pelacakan saldo harian (Daily Target Lock & Circuit Breaker)
    acc_init = mt5.account_info()
    initial_balance = acc_init.balance if acc_init else 0.0
    current_trade_date = datetime.now().date()
    daily_lock_triggered = False
    lock_reason = ""
    last_lock_log_time = 0.0

    if acc_init:
        curr_code = getattr(acc_init, "currency", "USD").upper()
        curr_symbol = "Rp" if curr_code == "IDR" else "$"
        logger.info(f"[MONEY MGT] Saldo Awal Hari Ini: {curr_symbol}{initial_balance:,.2f} {curr_code} (Tanggal: {current_trade_date})")
    
    try:
        while True:
            # 0. Pengecekan pergantian tanggal (reset harian otomatis pukul 00:00)
            today = datetime.now().date()
            if today != current_trade_date:
                current_trade_date = today
                fresh_acc = mt5.account_info()
                if fresh_acc:
                    initial_balance = fresh_acc.balance
                daily_lock_triggered = False
                set_circuit_breaker_lock(False, "")
                lock_reason = ""
                last_lock_log_time = 0.0
                curr_code = getattr(fresh_acc, "currency", "USD").upper() if fresh_acc else "USD"
                curr_symbol = "Rp" if curr_code == "IDR" else "$"
                logger.info(f"[RESET HARIAN] Tanggal berganti ({today}). Saldo awal di-reset: {curr_symbol}{initial_balance:,.2f}. Target harian aktif kembali.")

            # Pengecekan status pasar
            if not is_market_open(symbol):
                logger.info("[INFO] Pasar sedang tutup. Menunggu...")
                time.sleep(60)
                continue
            
            # 1. Pemantauan Real-Time Tick untuk Auto Break-Even (BEP / Lock Profit)
            check_and_apply_bep(symbol)

            # 2. Pelacakan & Evaluasi Daily Target Profit Lock / Circuit Breaker
            if settings.ENABLE_DAILY_TARGET_LOCK:
                daily_pnl_idr, is_idr_account, curr_code, raw_pnl = get_daily_realized_pnl()

                if not daily_lock_triggered:
                    # Evaluasi Kondisi 1: Target Profit Tercapai
                    if daily_pnl_idr >= settings.DAILY_TARGET_PROFIT_IDR:
                        daily_lock_triggered = True
                        lock_reason = f"[TARGET REACHED] Target profit harian tercapai (+Rp{daily_pnl_idr:,.0f})! Bot mengunci keuntungan dan berhenti trading untuk hari ini."
                        logger.info(lock_reason)
                        set_circuit_breaker_lock(True, lock_reason)

                    # Evaluasi Kondisi 2: Max Loss Terbentur / Circuit Breaker
                    elif daily_pnl_idr <= -settings.DAILY_MAX_LOSS_IDR:
                        daily_lock_triggered = True
                        daily_loss_idr = abs(daily_pnl_idr)
                        lock_reason = (
                            f"[CIRCUIT BREAKER LOCKED] Kerugian harian akumulasi (Rp{daily_loss_idr:,.0f}) "
                            f"telah melampaui batas toleransi (Rp{settings.DAILY_MAX_LOSS_IDR:,.0f}). "
                            f"Seluruh aktivitas trading baru DITUTUP untuk hari ini!"
                        )
                        logger.error(lock_reason)
                        set_circuit_breaker_lock(True, lock_reason)

                if daily_lock_triggered:
                    # Keselarasan posisi mengambang:
                    # Jika masih ada posisi aktif yang berjalan, biarkan diselesaikan oleh SL/TP atau Auto BEP
                    open_bot_pos = get_active_positions(symbol)
                    if open_bot_pos:
                        time.sleep(1)
                        continue
                    else:
                        # Posisi sudah bersih/selesai. Istirahatkan bot hingga pergantian hari (00:00)
                        now_ts = time.time()
                        if now_ts - last_lock_log_time >= 60:
                            logger.info(f"{lock_reason} Menunggu reset pukul 00:00...")
                            last_lock_log_time = now_ts
                        time.sleep(1)
                        continue

            # 3. Deteksi candle baru (menghemat sumber daya komputasi analisa AI)
            new_candle, current_candle_time = is_new_candle(symbol, timeframe, last_candle_time)
            
            if new_candle:
                if last_candle_time != 0:
                    logger.info("=========================================")
                    logger.info("[INFO] Candle baru terbentuk. Memulai analisa...")
                
                last_candle_time = current_candle_time
                
                # Cek Daily Drawdown Limit (Kill Switch)
                if not check_daily_drawdown_limit():
                    continue # Lewati eksekusi hingga hari berganti (script tetap jalan mengecek waktu)
                
                # 2. Ambil data OHLCV historis untuk M1 dan M5
                df_m1 = get_historical_data(symbol, mt5.TIMEFRAME_M1, count=100)
                df_m5 = get_historical_data(symbol, mt5.TIMEFRAME_M5, count=100)
                
                if df_m1.empty or df_m5.empty:
                    logger.warning("[WARNING] Gagal mengambil data historis M1 atau M5.")
                    continue
                    
                mtf_data = {
                    "m1": df_m1,
                    "m5": df_m5
                }
                    
                # 3. Cek posisi aktif dari bot ini (berdasarkan Magic Number)
                bot_positions = get_active_positions(symbol)
                has_active_pos = len(bot_positions) > 0
                
                # Ambil tick terkini
                tick = get_current_tick(symbol)
                if not tick:
                    continue
                    
                # 4. Hasilkan sinyal dari strategi AI
                ai_decision = get_ai_decision(mtf_data, tick, bot_positions)
                action = ai_decision.get('action', 'HOLD')
                
                # 5. Eksekusi Berdasarkan Keputusan AI
                
                # Sinkronisasi ulang posisi riil setelah inferensi AI (mengantisipasi posisi tersentuh SL/TP saat inferensi 3-5 detik)
                current_positions = get_active_positions(symbol)
                has_active_pos = len(current_positions) > 0

                # Skenario Reversal Cerdas
                if has_active_pos:
                    for pos in current_positions:
                        pos_type = pos.type # 0 = BUY, 1 = SELL
                        ticket = pos.ticket
                        
                        # Tutup jika AI menyuruh CLOSE atau jika arah AI berkebalikan dengan posisi saat ini
                        should_reverse_close = False
                        if action == 'CLOSE':
                            should_reverse_close = True
                            logger.info(f"[REVERSAL] Perintah CLOSE mutlak dari AI! Menutup posisi (Tiket: {ticket})")
                        elif action == 'SELL' and pos_type == mt5.ORDER_TYPE_BUY:
                            should_reverse_close = True
                            logger.info(f"[REVERSAL] Arah berbalik! AI merekomendasikan SELL. Menutup posisi BUY (Tiket: {ticket}) terlebih dahulu.")
                        elif action == 'BUY' and pos_type == mt5.ORDER_TYPE_SELL:
                            should_reverse_close = True
                            logger.info(f"[REVERSAL] Arah berbalik! AI merekomendasikan BUY. Menutup posisi SELL (Tiket: {ticket}) terlebih dahulu.")
                            
                        if should_reverse_close:
                            if close_position(ticket):
                                # Re-fetch status setelah posisi ditutup
                                current_positions = get_active_positions(symbol)
                                has_active_pos = len(current_positions) > 0

                # Eksekusi Entry Baru (Hanya jika posisi kosong dan di bawah batas MAX_OPEN_POSITIONS)
                if action in ['BUY', 'SELL'] and not has_active_pos and len(current_positions) < settings.MAX_OPEN_POSITIONS:
                    # Ekstrak nilai SL dan TP secara aman dari dictionary
                    sl_price = ai_decision.get('sl_price', 0.0)
                    tp_price = ai_decision.get('tp_price', 0.0)
                    
                    # Kita asumsikan AI memberikan SL absolut yang logis. 
                    entry_price = tick.ask if action == 'BUY' else tick.bid
                    sl_dist_price = abs(entry_price - sl_price)
                    
                    # Kalkulasi lot langsung dari selisih harga absolut riil
                    lot = calculate_lot_size(symbol, sl_dist_price=sl_dist_price)
                    
                    # Pastikan lot valid dan tidak error (contoh AI ngaco SL = Entry)
                    if lot <= 0 or sl_dist_price <= 0:
                        logger.error("[ERROR] SL dari AI tidak valid atau memicu lot <= 0. Batal OP.")
                    else:
                        order_type = mt5.ORDER_TYPE_BUY if action == 'BUY' else mt5.ORDER_TYPE_SELL
                        comment = f"AI_{action}_{ai_decision.get('confidence', 0.0):.2f}"
                        open_position(symbol, order_type, lot, sl_price=sl_price, tp_price=tp_price, comment=comment)
            
            # Sleep singkat agar tidak membebani CPU
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("[STOP] Bot dihentikan oleh pengguna (Ctrl+C).")
    except Exception as e:
        logger.error(f"[ERROR] Error tak terduga pada siklus utama: {e}", exc_info=True)
    finally:
        shutdown_mt5()

if __name__ == "__main__":
    main()