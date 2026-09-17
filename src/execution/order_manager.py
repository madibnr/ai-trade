import MetaTrader5 as mt5
from utils.logger import logger
from config import settings
from src.data.market_data import get_symbol_info, get_current_tick
from datetime import datetime

def check_daily_drawdown_limit():
    """
    Mengecek apakah total kerugian (realized loss) hari ini telah menyentuh/melebihi
    persentase MAX_DAILY_LOSS_PERCENT dari saldo awal harian akun.
    Return True jika aman (bisa trading), False jika sudah mencapai batas Kill Switch.
    """
    account_info = mt5.account_info()
    if account_info is None:
        return True # Asumsikan aman jika gagal fetch

    # Set rentang waktu dari jam 00:00 hari ini sampai saat ini
    date_from = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    date_to = datetime.now()

    deals = mt5.history_deals_get(date_from, date_to)
    
    total_realized_pnl = 0.0
    if deals:
        for deal in deals:
            # Hitung deal khusus milik bot ini
            if deal.magic == settings.MAGIC_NUMBER and deal.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL):
                total_realized_pnl += deal.profit
                # Catatan: deal.profit sudah mencakup profit bersih (positif/negatif)

    # Jika profit hari ini masih positif atau impas, aman
    if total_realized_pnl >= 0:
        return True

    # Jika merugi, bandingkan dengan modal
    current_balance = account_info.balance
    # Pendekatan: Saldo awal hari ini = current_balance - total_realized_pnl (karena pnl negatif, jadi +)
    starting_balance = current_balance - total_realized_pnl 
    
    loss_percent = abs(total_realized_pnl) / starting_balance * 100
    
    if loss_percent >= settings.MAX_DAILY_LOSS_PERCENT:
        logger.error(f"[KILL SWITCH] Kerugian harian ({loss_percent:.2f}%) melampaui batas maksimal ({settings.MAX_DAILY_LOSS_PERCENT}%).")
        logger.error(f"Total Rugi Hari Ini: ${abs(total_realized_pnl):.2f}. Trading dihentikan sementara hingga besok.")
        return False
        
    return True

def handle_trade_error(result, action_type="OP"):
    """
    Menangani dan mencetak log khusus untuk berbagai kode error (retcode) MT5 yang lazim terjadi.
    """
    retcode = result.retcode
    comment = result.comment
    
    if retcode == 10027:
        logger.error(f"[ERROR] {action_type} Gagal! Algo Trading belum diaktifkan di terminal MT5! "
                     "Silakan klik tombol 'Algo Trading' di toolbar MT5 hingga berwarna hijau "
                     "dan centang opsi di Tools > Options > Expert Advisors.")
    elif retcode == 10014:
        logger.error(f"[ERROR] {action_type} Gagal! Volume/Lot tidak valid (Retcode: 10014). "
                     f"Cek rumus calculate_lot_size atau limit broker. Comment: {comment}")
    elif retcode == 10016:
        logger.error(f"[ERROR] {action_type} Gagal! Stop Loss (SL) atau Take Profit (TP) tidak valid (Retcode: 10016). "
                     f"Jarak mungkin terlalu dekat dengan harga saat ini. Comment: {comment}")
    elif retcode == 10019:
        acc = mt5.account_info()
        if acc:
            logger.error(f"[ERROR] {action_type} Gagal! Margin/Saldo tidak cukup (Retcode: 10019). "
                         f"Free Margin: ${acc.margin_free:.2f}, Margin Terpakai: ${acc.margin:.2f}, "
                         f"Balance: ${acc.balance:.2f}. Comment: {comment}")
        else:
            logger.error(f"[ERROR] {action_type} Gagal! Margin/Saldo tidak cukup untuk membuka posisi (Retcode: 10019). "
                         f"Comment: {comment}")
    elif retcode == 10015:
        logger.error(f"[ERROR] {action_type} Gagal! Harga entry tidak valid (Retcode: 10015). "
                     f"Comment: {comment}")
    elif retcode == 10030:
        logger.error(f"[ERROR] {action_type} Gagal! Tipe order filling tidak didukung (Retcode: 10030). "
                     f"Comment: {comment}")
    else:
        logger.error(f"[ERROR] {action_type} Gagal! Retcode: {retcode} - {comment}")

def get_filling_mode(symbol):
    """
    Menentukan mode eksekusi order (filling mode) secara dinamis sesuai spesifikasi broker.
    """
    symbol_info = get_symbol_info(symbol)
    if symbol_info is None:
        # Fallback default jika gagal baca info
        return mt5.ORDER_FILLING_IOC
        
    modes = symbol_info.filling_mode
    
    # Bit 0 = FOK (nilai 1), Bit 1 = IOC (nilai 2)
    if modes & 1:
        return mt5.ORDER_FILLING_FOK
    elif modes & 2:
        return mt5.ORDER_FILLING_IOC
    else:
        return mt5.ORDER_FILLING_RETURN

def validate_max_nominal_risk(symbol, lot_size, sl_dist_price):
    """
    Memvalidasi apakah potensi kerugian Stop Loss melampaui batas nominal uang riil (Rupiah).
    Menggunakan formula kalkulasi riil berbasis Contract Size untuk akurasi presisi tinggi.
    """
    symbol_info = get_symbol_info(symbol)
    if not symbol_info:
        return False
        
    # Ambil ukuran kontrak dari broker (Contoh: XAUUSD = 100, EURUSD = 100000)
    contract_size = symbol_info.trade_contract_size
    
    # Fallback aman jika broker tidak menyediakan data contract_size
    if contract_size == 0:
        if "XAU" in symbol.upper() or "GOLD" in symbol.upper():
            contract_size = 100.0
        else:
            contract_size = 100000.0
            
    # Kalkulasi Estimasi Kerugian Nominal dalam USD:
    # Jarak SL Absolut (dalam dolar/harga mutlak) * Volume Lot * Ukuran Kontrak
    projected_loss_usd = float(sl_dist_price) * float(lot_size) * float(contract_size)
    
    # Konversi ke nominal lokal (Rupiah)
    projected_loss_idr = projected_loss_usd * settings.KURS_USD_IDR
    
    if projected_loss_idr > settings.MAX_LOSS_IDR:
        logger.warning(
            f"[RISK REJECTED] Potensi rugi SL melebihi toleransi maksimal akun mikro! "
            f"Estimasi Loss: Rp{projected_loss_idr:,.0f} (USD ${projected_loss_usd:.2f}) > Batas: Rp{settings.MAX_LOSS_IDR:,.0f}. "
            f"Jarak SL: ${sl_dist_price:.2f}. Order OP Dibatalkan secara aman."
        )
        return False
        
    return True

def calculate_lot_size(symbol, sl_dist_price, risk_percent=None):
    """
    Kalkulasi lot size dinamis berdasarkan risiko (default 1%).
    Menggunakan pembagian poin riil broker dari selisih harga absolut.
    """
    if risk_percent is None:
        risk_percent = settings.RISK_PERCENT
        
    account_info = mt5.account_info()
    if account_info is None:
        logger.warning("[WARNING] Gagal membaca balance akun. Menggunakan lot minimum.")
        return settings.MAX_LOT

    symbol_info = get_symbol_info(symbol)
    if symbol_info is None or symbol_info.point == 0:
        return settings.MAX_LOT

    balance = account_info.balance
    risk_amount = balance * (risk_percent / 100)
    
    tick_value = symbol_info.trade_tick_value
    sl_ticks = sl_dist_price / symbol_info.point
    
    if tick_value == 0 or sl_ticks <= 0:
        logger.warning("[WARNING] Tick Value atau SL 0. Menggunakan lot minimum.")
        return symbol_info.volume_min

    try:
        raw_lot = risk_amount / (sl_ticks * tick_value)
        # Pembulatan sesuai step broker
        final_lot = round(raw_lot / symbol_info.volume_step) * symbol_info.volume_step
        # Membatasi dengan Min broker dan Max limit dari pengguna
        max_allowed_lot = min(symbol_info.volume_max, settings.MAX_LOT)
        final_lot = max(symbol_info.volume_min, min(max_allowed_lot, final_lot))
        final_lot = float(round(final_lot, 2))
        
        logger.info(f"[RISK MGT] Balance: ${balance:.2f} | Risk: {risk_percent}% (${risk_amount:.2f}) | SL Jarak: {sl_ticks:.1f} ticks | Final Lot: {final_lot} lot")
        return final_lot
    except ZeroDivisionError:
        return symbol_info.volume_min

def get_active_positions(symbol):
    """
    Mengambil posisi aktif untuk simbol tertentu dan Magic Number dari bot ini.
    Dilengkapi fallback pencocokan toleran untuk broker dengan akhiran simbol khusus (misal: XAUUSDb / XAUUSD.m).
    """
    positions = mt5.positions_get(symbol=symbol)
    if positions is None or len(positions) == 0:
        all_positions = mt5.positions_get()
        if all_positions:
            positions = [p for p in all_positions if symbol.upper() in p.symbol.upper()]
        else:
            positions = []
            
    bot_positions = [pos for pos in positions if pos.magic == settings.MAGIC_NUMBER]
    return bot_positions

def open_position(symbol, order_type, lot_size, sl_price=0.0, tp_price=0.0, comment="Bot Order"):
    """
    Membuka posisi BUY atau SELL dengan validasi Spread, Stops Level, dan Anti-Stacking Guard.
    """
    # 0. Validasi Anti-Stacking (Maksimal Posisi Simultan)
    current_active = get_active_positions(symbol)
    if len(current_active) >= settings.MAX_OPEN_POSITIONS:
        logger.warning(f"[WARNING] OP Dibatalkan! Maksimal posisi aktif ({settings.MAX_OPEN_POSITIONS}) telah tercapai.")
        return None

    tick = get_current_tick(symbol)
    if tick is None:
        logger.error("[ERROR] Gagal mendapatkan data tick saat mau open position.")
        return None

    symbol_info = get_symbol_info(symbol)
    if symbol_info is None:
        return None
        
    # 1. Validasi Maksimal Spread
    current_spread = symbol_info.spread
    if current_spread > settings.MAX_SPREAD_POINTS:
        logger.warning(f"[WARNING] OP Dibatalkan! Spread saat ini ({current_spread}) melebihi batas maksimal ({settings.MAX_SPREAD_POINTS}).")
        return None

    price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid
    filling_type = get_filling_mode(symbol)

    # 2. Validasi Jarak Minimum SL dan TP (Trade Stops Level)
    stops_level = symbol_info.trade_stops_level * symbol_info.point
    
    if order_type == mt5.ORDER_TYPE_BUY:
        if sl_price > 0 and (price - sl_price) < stops_level:
            logger.warning(f"[WARNING] Jarak SL terlalu dekat. Disesuaikan otomatis ke batas broker.")
            sl_price = price - stops_level
        if tp_price > 0 and (tp_price - price) < stops_level:
            logger.warning(f"[WARNING] Jarak TP terlalu dekat. Disesuaikan otomatis ke batas broker.")
            tp_price = price + stops_level
            
    elif order_type == mt5.ORDER_TYPE_SELL:
        if sl_price > 0 and (sl_price - price) < stops_level:
            logger.warning(f"[WARNING] Jarak SL terlalu dekat. Disesuaikan otomatis ke batas broker.")
            sl_price = price + stops_level
        if tp_price > 0 and (price - tp_price) < stops_level:
            logger.warning(f"[WARNING] Jarak TP terlalu dekat. Disesuaikan otomatis ke batas broker.")
            tp_price = price - stops_level

    # 3. Validasi Margin Pra-Eksekusi (Pre-Trade Margin Check)
    account_info = mt5.account_info()
    if account_info is not None:
        required_margin = mt5.order_calc_margin(order_type, symbol, float(lot_size), price)
        if required_margin is not None:
            if account_info.margin_free < required_margin:
                logger.warning(f"[WARNING] Margin tidak cukup untuk buka posisi! "
                               f"Dibutuhkan: ${required_margin:.2f}, Free Margin: ${account_info.margin_free:.2f}. "
                               "Order dibatalkan secara aman.")
                return None

    # 4. Validasi Batas Maksimal Kerugian Riil (Hard Stop Nominal Guard)
    sl_dist_real = abs(price - sl_price) if sl_price > 0 else 0
    if sl_dist_real > 0:
        if not validate_max_nominal_risk(symbol, lot_size, sl_dist_real):
            return None # Batalkan eksekusi jika menabrak batas uang tunai

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lot_size),
        "type": order_type,
        "price": price,
        "sl": float(sl_price),
        "tp": float(tp_price),
        "deviation": settings.MAX_SLIPPAGE,
        "magic": settings.MAGIC_NUMBER,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_type,
    }

    result = mt5.order_send(request)
    
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        handle_trade_error(result, action_type="OP")
        return None
        
    # Verifikasi ke server untuk memastikan data riil yang tereksekusi
    confirmed_pos = mt5.positions_get(ticket=result.order)
    if confirmed_pos and len(confirmed_pos) > 0:
        real_pos = confirmed_pos[0]
        real_volume = real_pos.volume
        real_price = real_pos.price_open
        real_sl = real_pos.sl
        real_tp = real_pos.tp
    else:
        # Jika gagal fetch (jarang terjadi), gunakan data dari result
        real_volume = result.volume
        real_price = result.price
        real_sl = sl_price
        real_tp = tp_price
        
    tipe_str = 'BUY' if order_type == mt5.ORDER_TYPE_BUY else 'SELL'
    logger.info(f"[ORDER EXECUTED] Tiket: {result.order} | Aksi: {tipe_str} | Volume Lot Riil: {real_volume} lot | Harga Entry: {real_price} | SL: {real_sl} | TP: {real_tp}")
    return result

def close_position(ticket):
    """
    Menutup posisi aktif berdasarkan tiket (CP).
    Tahan terhadap race condition jika posisi sudah tertutup lebih dulu oleh server (SL/TP).
    """
    position = mt5.positions_get(ticket=ticket)
    if position is None or len(position) == 0:
        logger.info(f"[INFO] Posisi tiket {ticket} sudah tidak aktif di terminal (kemungkinan telah tersentuh SL/TP atau ditutup broker).")
        return True # Posisi sudah tidak ada di terminal, tujuan penutupan tercapai
        
    pos = position[0]
    symbol = pos.symbol
    tick = get_current_tick(symbol)
    
    if tick is None:
        return False
        
    order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
    filling_type = get_filling_mode(symbol)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": pos.volume,
        "type": order_type,
        "position": ticket,
        "price": price,
        "deviation": settings.MAX_SLIPPAGE,
        "magic": settings.MAGIC_NUMBER,
        "comment": "Close Pos By Reversal",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_type,
    }

    result = mt5.order_send(request)
    
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        handle_trade_error(result, action_type=f"CP (Tiket {ticket})")
        return False
        
    logger.info(f"[SUCCESS] CP Berhasil! Tiket {ticket} ditutup pada harga {result.price}. Volume: {pos.volume}")
    return True

def check_and_apply_bep(symbol: str) -> None:
    """
    Memeriksa posisi aktif bot dan menggeser Stop Loss ke level Break-Even (Lock Profit)
    secara otomatis jika floating profit mencapai ambang batas BEP_TRIGGER_PROFIT_IDR.
    Mendukung deteksi otomatis mata uang akun (IDR vs USD) dan validasi ketat harga pasar.
    """
    if not settings.ENABLE_AUTO_BEP:
        return

    positions = get_active_positions(symbol)
    if not positions:
        return

    symbol_info = get_symbol_info(symbol)
    if not symbol_info:
        return

    tick = get_current_tick(symbol)
    if not tick:
        return

    # Deteksi mata uang akun MT5 secara dinamis
    account = mt5.account_info()
    is_idr_account = False
    if account and getattr(account, "currency", "").upper() == "IDR":
        is_idr_account = True

    stops_level_dist = max(symbol_info.trade_stops_level * symbol_info.point, symbol_info.point)

    for pos in positions:
        # Hitung floating profit nominal sesuai mata uang akun
        if is_idr_account:
            floating_profit_idr = pos.profit
            profit_display_str = f"Rp{floating_profit_idr:,.0f}"
        else:
            floating_profit_idr = pos.profit * float(getattr(settings, "KURS_USD_IDR", 16000.0))
            profit_display_str = f"Rp{floating_profit_idr:,.0f} (${pos.profit:.2f})"

        # Cek apakah sudah menyentuh ambang profit pemicu BEP (misal: Rp20.000)
        if floating_profit_idr >= settings.BEP_TRIGGER_PROFIT_IDR:
            current_sl = pos.sl
            price_open = pos.price_open
            pos_type = pos.type # 0 = BUY, 1 = SELL

            new_sl = 0.0
            should_modify = False

            if pos_type == mt5.ORDER_TYPE_BUY:
                target_sl = round(price_open + settings.BEP_LOCK_OFFSET_PRICE, symbol_info.digits)
                # Syarat BUY: Harga Bid pasar harus sudah berada AMAN di atas target_sl plus stop distance,
                # dan SL saat ini masih berada di bawah target_sl (belum dimodifikasi).
                if (tick.bid - target_sl) >= stops_level_dist and current_sl < target_sl:
                    new_sl = target_sl
                    should_modify = True

            elif pos_type == mt5.ORDER_TYPE_SELL:
                target_sl = round(price_open - settings.BEP_LOCK_OFFSET_PRICE, symbol_info.digits)
                # Syarat SELL: Harga Ask pasar harus sudah berada AMAN di bawah target_sl minus stop distance,
                # dan SL saat ini masih berada di atas target_sl atau belum ada SL (0.0).
                if (target_sl - tick.ask) >= stops_level_dist and (current_sl > target_sl or current_sl == 0.0):
                    new_sl = target_sl
                    should_modify = True

            if should_modify and new_sl > 0.0:
                request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "position": pos.ticket,
                    "symbol": pos.symbol,
                    "sl": new_sl,
                    "tp": pos.tp
                }
                result = mt5.order_send(request)
                if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info(
                        f"[BEP TRIGGERED] Tiket {pos.ticket} berhasil diproteksi! "
                        f"Floating Profit: {profit_display_str} | "
                        f"SL digeser ke {new_sl:.2f} (Locked Profit)."
                    )
                else:
                    err_msg = f"Retcode: {result.retcode} - {result.comment}" if result else "No response"
                    logger.warning(f"[WARNING] Gagal memodifikasi BEP untuk tiket {pos.ticket}. {err_msg}")