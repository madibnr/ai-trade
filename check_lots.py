import MetaTrader5 as mt5
from datetime import datetime, timedelta
from config import settings
from src.data.mt5_connection import initialize_mt5, shutdown_mt5

def print_separator():
    print("-" * 80)

def get_type_str(type_int):
    if type_int == 0:
        return "BUY"
    elif type_int == 1:
        return "SELL"
    return "UNKNOWN"

def check_open_positions():
    print_separator()
    print(">>> DAFTAR POSISI TERBUKA (OPEN POSITIONS) SAAT INI")
    print_separator()
    
    positions = mt5.positions_get()
    if positions is None or len(positions) == 0:
        print("Tidak ada posisi yang terbuka di terminal.")
        return
        
    print(f"{'TIKET':<12} | {'SIMBOL':<8} | {'TIPE':<6} | {'LOT':<6} | {'HARGA OP':<10} | {'SL':<10} | {'TP':<10} | {'PROFIT FLOATING'}")
    print_separator()
    
    for pos in positions:
        tipe = get_type_str(pos.type)
        marker = "*" if pos.magic == settings.MAGIC_NUMBER else ""
        print(f"{pos.ticket:<12} | {pos.symbol:<8} | {tipe:<6} | {pos.volume:<6} | {pos.price_open:<10.2f} | {pos.sl:<10.2f} | {pos.tp:<10.2f} | ${pos.profit:.2f} {marker}")
        
    print("\nCatatan: Tanda bintang (*) menandakan posisi milik bot ini (Magic Number).")

def check_recent_history():
    print_separator()
    print(">>> 5 RIWAYAT TRANSAKSI TERAKHIR (REALIZED HISTORY) DARI BOT")
    print_separator()
    
    # Ambil riwayat dari 30 hari yang lalu hingga sekarang
    date_from = datetime.now() - timedelta(days=30)
    date_to = datetime.now() + timedelta(days=1) # +1 hari untuk batas aman zona waktu
    
    # mt5.history_deals_get mengembalikan transaksi riil (Deal)
    deals = mt5.history_deals_get(date_from, date_to)
    
    if deals is None or len(deals) == 0:
        print("Tidak ada riwayat transaksi yang ditemukan.")
        return
        
    # Filter deal khusus milik bot dan urutkan dari yang terbaru (Deal type 0 dan 1 = BUY/SELL)
    bot_deals = [d for d in deals if d.magic == settings.MAGIC_NUMBER and d.type in (0, 1)]
    
    if not bot_deals:
        print(f"Tidak ada riwayat transaksi milik bot (Magic: {settings.MAGIC_NUMBER}).")
        return
        
    # Urutkan berdasarkan waktu, ambil 5 terakhir
    bot_deals.sort(key=lambda x: x.time, reverse=True)
    recent_deals = bot_deals[:5]
    
    print(f"{'WAKTU EKSEKUSI':<20} | {'TIKET DEAL':<12} | {'SIMBOL':<8} | {'TIPE':<6} | {'LOT':<6} | {'HARGA':<10} | {'PROFIT/RUGI'}")
    print_separator()
    
    for deal in recent_deals:
        waktu = datetime.fromtimestamp(deal.time).strftime('%Y-%m-%d %H:%M:%S')
        tipe = get_type_str(deal.type)
        print(f"{waktu:<20} | {deal.ticket:<12} | {deal.symbol:<8} | {tipe:<6} | {deal.volume:<6} | {deal.price:<10.2f} | ${deal.profit:.2f}")

def main():
    print("\n🚀 Menghubungkan ke MetaTrader 5 untuk Audit Data...")
    if not initialize_mt5():
        print("Gagal terhubung ke terminal MT5.")
        return
        
    check_open_positions()
    print("\n")
    check_recent_history()
    print_separator()
    
    shutdown_mt5()
    print("✅ Audit selesai. Terminal diputus.\n")

if __name__ == "__main__":
    main()