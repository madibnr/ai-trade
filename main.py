import time
import MetaTrader5 as mt5
from config import settings
from utils.logger import logger
from src.data.mt5_connection import initialize_mt5, shutdown_mt5
from src.data.market_data import get_historical_data, is_market_open, get_symbol_info, get_current_tick
from src.strategy.ai_strategy import get_ai_decision
from src.execution.order_manager import calculate_lot_size, open_position, close_position, get_active_positions, check_daily_drawdown_limit

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
    timeframe = TF_MAP.get(settings.TIMEFRAME_STR, mt5.TIMEFRAME_M15)
    
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
    
    try:
        while True:
            # Pengecekan status pasar
            if not is_market_open(symbol):
                logger.info("[INFO] Pasar sedang tutup. Menunggu...")
                time.sleep(60)
                continue
            
            # Deteksi candle baru (menghemat sumber daya komputasi)
            new_candle, current_candle_time = is_new_candle(symbol, timeframe, last_candle_time)
            
            if new_candle:
                if last_candle_time != 0:
                    logger.info("=========================================")
                    logger.info("[INFO] Candle baru terbentuk. Memulai analisa...")
                
                last_candle_time = current_candle_time
                
                # Cek Daily Drawdown Limit (Kill Switch)
                if not check_daily_drawdown_limit():
                    continue # Lewati eksekusi hingga hari berganti (script tetap jalan mengecek waktu)
                
                # 2. Ambil data OHLCV historis
                df = get_historical_data(symbol, timeframe, count=100)
                if df.empty:
                    continue
                    
                # 3. Cek posisi aktif dari bot ini (berdasarkan Magic Number)
                bot_positions = get_active_positions(symbol)
                has_active_pos = len(bot_positions) > 0
                
                # Ambil tick terkini
                tick = get_current_tick(symbol)
                if not tick:
                    continue
                    
                # 4. Hasilkan sinyal dari strategi AI
                ai_decision = get_ai_decision(df, tick, bot_positions)
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