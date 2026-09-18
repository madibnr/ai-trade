import time
from datetime import datetime
import MetaTrader5 as mt5
from utils.logger import logger
from config import settings
from src.data.market_data import get_symbol_info, get_current_tick, get_symbol_metadata

try:
    from src.web.server import update_dashboard_state, add_dashboard_event
except ImportError:
    def update_dashboard_state(category: str, data: dict) -> None:
        pass
    def add_dashboard_event(event_type: str, message: str) -> None:
        pass

# Circuit Breaker state cache
_circuit_breaker_locked = False
_circuit_breaker_reason = ""

def set_circuit_breaker_lock(locked: bool, reason: str = ""):
    """Mengaktifkan atau menonaktifkan status penguncian Circuit Breaker / Target Lock."""
    global _circuit_breaker_locked, _circuit_breaker_reason
    _circuit_breaker_locked = locked
    _circuit_breaker_reason = reason

def is_circuit_breaker_locked():
    """Mengecek status terkunci Circuit Breaker."""
    return _circuit_breaker_locked, _circuit_breaker_reason

def evaluate_daily_pnl_limits(
    current_balance: float,
    initial_balance: float,
    is_idr_account: bool,
    kurs_usd_idr: float = 16000.0,
    target_profit_idr: float = None,
    max_loss_idr: float = None
):
    """
    Mengevaluasi apakah PnL harian telah menyentuh target laba atau batas kerugian darurat.
    Mengembalikan tuple: (can_trade: bool, status: str, daily_pnl_idr: float)
    - status: 'NORMAL' | 'TARGET_LOCKED' | 'CIRCUIT_BREAKER'
    """
    balance_diff = current_balance - initial_balance
    daily_pnl_idr = balance_diff if is_idr_account else balance_diff * kurs_usd_idr
    
    target_profit = target_profit_idr if target_profit_idr is not None else settings.DAILY_TARGET_PROFIT_IDR
    max_loss = max_loss_idr if max_loss_idr is not None else settings.DAILY_MAX_LOSS_IDR
    
    if not getattr(settings, "ENABLE_DAILY_TARGET_LOCK", True):
        return True, "NORMAL", daily_pnl_idr
        
    if daily_pnl_idr >= target_profit:
        return False, "TARGET_LOCKED", daily_pnl_idr
    elif daily_pnl_idr <= -max_loss:
        return False, "CIRCUIT_BREAKER", daily_pnl_idr
        
    return True, "NORMAL", daily_pnl_idr

def get_bep_offset_price(metadata: dict, base_offset: float) -> float:
    """
    Menghitung jarak offset penguncian BEP secara adaptif multi-aset:
    - Pasangan Forex (digits >= 3): mengunci 2.0 pips (20 point pada broker 5 digit).
    - Emas / Kripto / Indeks (digits <= 2): menggunakan nominal dolar (default 0.20 untuk XAUUSD).
    """
    digits = metadata["digits"]
    point = metadata["point"]

    if digits >= 3:
        if base_offset >= 0.01:
            offset = 20.0 * point
        else:
            offset = float(base_offset)
    else:
        offset = float(base_offset)

    return max(offset, point)

def get_daily_realized_pnl(symbol: str = None):
    """
    Mengambil total PnL yang sudah terealisasi hari ini (sejak 00:00) khusus untuk bot ini (berdasarkan magic number dan simbol).
    Mendukung deteksi otomatis mata uang akun (IDR vs USD):
    - Jika IDR: PnL dari riwayat deal SUDAH dalam satuan Rupiah (JANGAN dikalikan KURS_USD_IDR).
    - Jika USD: PnL masih berupa Dolar, dikonversikan ke IDR dengan KURS_USD_IDR.
    Mengembalikan tuple: (daily_pnl_idr: float, is_idr_account: bool, currency: str, raw_pnl: float)
    """
    acc = mt5.account_info()
    if acc is None:
        return 0.0, False, "USD", 0.0

    currency = getattr(acc, "currency", "USD").upper()
    is_idr_account = currency == "IDR"

    date_from = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    date_to = datetime.now()

    target_symbol = symbol or getattr(settings, "SYMBOL", "")

    deals = mt5.history_deals_get(date_from, date_to)
    total_realized_pnl = 0.0
    if deals:
        for deal in deals:
            if deal.magic == settings.MAGIC_NUMBER and deal.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT, mt5.DEAL_ENTRY_OUT_BY):
                if target_symbol:
                    if target_symbol.upper() not in deal.symbol.upper() and deal.symbol.upper() not in target_symbol.upper():
                        continue
                # Hitung PnL Bersih Riil: Profit Kotor + Komisi + Swap + Fee
                deal_net = deal.profit + deal.commission + deal.swap + getattr(deal, 'fee', 0.0)
                total_realized_pnl += deal_net

    raw_daily_pnl = float(total_realized_pnl)
    if is_idr_account:
        daily_pnl_idr = raw_daily_pnl
    else:
        daily_pnl_idr = raw_daily_pnl * float(getattr(settings, "KURS_USD_IDR", 16000.0))

    return daily_pnl_idr, is_idr_account, currency, raw_daily_pnl

def check_daily_drawdown_limit():
    """
    Mengecek apakah total kerugian (realized loss) hari ini telah menyentuh/melebihi
    DAILY_MAX_LOSS_IDR dari akun.
    Mendukung deteksi otomatis mata uang akun (IDR vs USD) secara dinamis.
    Return True jika aman (bisa trading), False jika sudah mencapai batas Circuit Breaker.
    """
    if not getattr(settings, "ENABLE_DAILY_TARGET_LOCK", True):
        return True

    daily_pnl_idr, is_idr_account, currency, raw_pnl = get_daily_realized_pnl()

    # Jika profit positif atau impas, aman
    if daily_pnl_idr >= 0:
        return True

    daily_loss_idr = abs(daily_pnl_idr)

    if daily_loss_idr >= settings.DAILY_MAX_LOSS_IDR:
        logger.error(
            f"[CIRCUIT BREAKER LOCKED] Kerugian harian akumulasi (Rp{daily_loss_idr:,.0f}) "
            f"telah melampaui batas toleransi (Rp{settings.DAILY_MAX_LOSS_IDR:,.0f}). "
            f"Seluruh aktivitas trading baru DITUTUP untuk hari ini!"
        )
        set_circuit_breaker_lock(
            True,
            f"[CIRCUIT BREAKER LOCKED] Kerugian harian akumulasi (Rp{daily_loss_idr:,.0f}) telah melampaui batas toleransi (Rp{settings.DAILY_MAX_LOSS_IDR:,.0f})"
        )
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
            curr_code = getattr(acc, "currency", "USD").upper()
            curr_symbol = "Rp" if curr_code == "IDR" else "$"
            logger.error(f"[ERROR] {action_type} Gagal! Margin/Saldo tidak cukup (Retcode: 10019). "
                         f"Free Margin: {curr_symbol}{acc.margin_free:,.2f}, Margin Terpakai: {curr_symbol}{acc.margin:,.2f}, "
                         f"Balance: {curr_symbol}{acc.balance:,.2f}. Comment: {comment}")
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
        
        curr_code = getattr(account_info, "currency", "USD").upper()
        curr_symbol = "Rp" if curr_code == "IDR" else "$"
        logger.info(f"[RISK MGT] Balance: {curr_symbol}{balance:,.2f} | Risk: {risk_percent}% ({curr_symbol}{risk_amount:,.2f}) | SL Jarak: {sl_ticks:.1f} ticks | Final Lot: {final_lot} lot")
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
    Membuka posisi BUY atau SELL dengan validasi Circuit Breaker, Anti-Stacking, Spread, dan Stops Level.
    """
    # 0. Validasi Circuit Breaker / Daily Target Lock
    is_locked, lock_reason = is_circuit_breaker_locked()
    if is_locked:
        logger.warning(f"[BLOCKED by Daily Circuit Breaker] OP Ditolak! Alasan: {lock_reason}")
        return None

    # 1. Validasi Anti-Stacking (Maksimal Posisi Simultan)
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

    metadata = get_symbol_metadata(symbol)
    digits = metadata["digits"]
    stops_level = metadata["stops_level_dist"]

    price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid
    price = round(price, digits)
    filling_type = get_filling_mode(symbol)

    # 2. Kunci Pengaman Matematis: Batas Atas SL M1 ($2.50 untuk XAU) & Enforce R:R Minimal 1:1.2
    if sl_price > 0:
        is_gold = "XAU" in symbol.upper() or "GOLD" in symbol.upper()
        if order_type == mt5.ORDER_TYPE_BUY and sl_price >= price:
            sl_price = round(price - (2.0 if is_gold else 40 * metadata["point"]), digits)
        elif order_type == mt5.ORDER_TYPE_SELL and sl_price <= price:
            sl_price = round(price + (2.0 if is_gold else 40 * metadata["point"]), digits)

        sl_distance = abs(price - sl_price)
        sl_cap = getattr(settings, "SL_CAP_PRICE", 2.50 if is_gold else 0.0025)

        if sl_distance > sl_cap:
            if order_type == mt5.ORDER_TYPE_BUY:
                sl_price = round(price - sl_cap, digits)
            elif order_type == mt5.ORDER_TYPE_SELL:
                sl_price = round(price + sl_cap, digits)
            sl_distance = abs(price - sl_price)
            logger.info(f"[SL CAP GUARD] SL dikoreksi maksimal ke jarak {sl_cap:.{digits}f} dari entry ({sl_price:.{digits}f}).")

        if tp_price > 0:
            tp_distance = abs(tp_price - price)
            min_rr = getattr(settings, "MIN_RR_RATIO", 1.5)
            min_tp_dist = sl_distance * min_rr
            if tp_distance < min_tp_dist or (order_type == mt5.ORDER_TYPE_BUY and tp_price <= price) or (order_type == mt5.ORDER_TYPE_SELL and tp_price >= price):
                if order_type == mt5.ORDER_TYPE_BUY:
                    tp_price = round(price + min_tp_dist, digits)
                elif order_type == mt5.ORDER_TYPE_SELL:
                    tp_price = round(price - min_tp_dist, digits)
                logger.info(f"[R:R GUARD] TP disesuaikan otomatis ke {tp_price:.{digits}f} agar memenuhi syarat minimal R:R 1:{min_rr:.1f}.")

    # 3. Validasi Jarak Minimum SL dan TP Terhadap Harga Eksekusi Riil Broker
    # Kepatuhan MT5: BUY ditutup pada BID, SELL ditutup pada ASK!
    if order_type == mt5.ORDER_TYPE_BUY:
        if sl_price <= 0:
            logger.error("[REJECTED] Order BUY ditolak: Stop Loss wajib bernilai positif (> 0)!")
            return None
        # SL BUY wajib berada di bawah BID minimal sejauh stops_level
        if (tick.bid - sl_price) < stops_level:
            sl_price = round(tick.bid - stops_level, digits)
            logger.warning(f"[STOPS ADJUST] SL BUY disesuaikan ke {sl_price:.{digits}f} agar mematuhi batasan Bid broker.")
        # TP BUY wajib berada di atas BID minimal sejauh stops_level
        if tp_price > 0 and (tp_price - tick.bid) < stops_level:
            tp_price = round(tick.bid + stops_level, digits)
            logger.warning(f"[STOPS ADJUST] TP BUY disesuaikan ke {tp_price:.{digits}f} agar mematuhi batasan Bid broker.")
            
    elif order_type == mt5.ORDER_TYPE_SELL:
        if sl_price <= 0:
            logger.error("[REJECTED] Order SELL ditolak: Stop Loss wajib bernilai positif (> 0)!")
            return None
        # SL SELL wajib berada di atas ASK minimal sejauh stops_level
        if (sl_price - tick.ask) < stops_level:
            sl_price = round(tick.ask + stops_level, digits)
            logger.warning(f"[STOPS ADJUST] SL SELL disesuaikan ke {sl_price:.{digits}f} agar mematuhi batasan Ask broker.")
        # TP SELL wajib berada di bawah ASK minimal sejauh stops_level
        if tp_price > 0 and (tick.ask - tp_price) < stops_level:
            tp_price = round(tick.ask - stops_level, digits)
            logger.warning(f"[STOPS ADJUST] TP SELL disesuaikan ke {tp_price:.{digits}f} agar mematuhi batasan Ask broker.")

    # Pastikan pembulatan harga SL dan TP sesuai dengan digits simbol
    sl_price = round(sl_price, digits) if sl_price > 0 else 0.0
    tp_price = round(tp_price, digits) if tp_price > 0 else 0.0

    # 3. Validasi Margin Pra-Eksekusi (Pre-Trade Margin Check)
    account_info = mt5.account_info()
    if account_info is not None:
        required_margin = mt5.order_calc_margin(order_type, symbol, float(lot_size), price)
        if required_margin is not None:
            if account_info.margin_free < required_margin:
                curr_code = getattr(account_info, "currency", "USD").upper()
                curr_symbol = "Rp" if curr_code == "IDR" else "$"
                logger.warning(f"[WARNING] Margin tidak cukup untuk buka posisi! "
                               f"Dibutuhkan: {curr_symbol}{required_margin:,.2f}, Free Margin: {curr_symbol}{account_info.margin_free:,.2f}. "
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
    logger.info(f"[ORDER EXECUTED] Tiket: {result.order} | Aksi: {tipe_str} | Volume Lot Riil: {real_volume} lot | Harga Entry: {real_price:.{digits}f} | SL: {real_sl:.{digits}f} | TP: {real_tp:.{digits}f}")
    add_dashboard_event("ORDER", f"OP {tipe_str} {real_volume:.2f} lot @ {real_price:.{digits}f} (Tiket: {result.order})")
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
        add_dashboard_event("ERROR", f"Gagal tutup tiket {ticket} ({result.comment})")
        return False
        
    logger.info(f"[SUCCESS] CP Berhasil! Tiket {ticket} ditutup pada harga {result.price}. Volume: {pos.volume}")
    add_dashboard_event("ORDER", f"CP Tiket {ticket} ditutup @ {result.price:.{digits}f}")
    return True

# State tracker untuk pengawalan bertingkat (Dynamic Stepped Trade Management)
position_states = {}

def modify_position_sltp(ticket: int, symbol: str, sl: float, tp: float):
    """
    Mengirimkan request TRADE_ACTION_SLTP ke MT5 untuk memodifikasi SL dan TP suatu posisi aktif.
    """
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol": symbol,
        "sl": float(sl),
        "tp": float(tp)
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err_comment = result.comment if result else "No response"
        err_code = result.retcode if result else 0
        logger.warning(f"[WARNING] Gagal modifikasi SL/TP tiket {ticket} ke SL:{sl}, TP:{tp}. Code: {err_code} - {err_comment}")
    return result

def manage_open_positions(symbol: str) -> None:
    """
    Pengawalan posisi dinamis bertingkat (Dynamic Stepped Trade Management) real-time:
    - Tahap 1: Standard Auto BEP (menggeser SL ke titik impas + offset komisi)
    - Tahap 2: Stagnant Profit Lock (mengunci sebagian besar profit jika harga tertahan)
    - Tahap 3: Dynamic TP Extender / Runner (memperpanjang TP dan menarik SL jika momentum kencang)
    """
    # Validasi koneksi terminal MT5 sebelum sinkronisasi state tracker
    terminal_info = mt5.terminal_info()
    if terminal_info is None or not getattr(terminal_info, "connected", True):
        return

    # Periksa error MT5 sebelum menganggap akun flat
    raw_positions = mt5.positions_get(symbol=symbol)
    if raw_positions is None and mt5.last_error()[0] != 1:
        # Micro-disconnect atau IPC sibuk: jangan hapus state tracker!
        return

    positions = get_active_positions(symbol)
    if not positions:
        position_states.clear()
        update_dashboard_state("active_position", {
            "has_position": False,
            "ticket": 0,
            "type": "-",
            "lot": 0.0,
            "open_price": 0.0,
            "current_sl": 0.0,
            "current_tp": 0.0,
            "floating_pnl_idr": 0.0,
            "stepped_stage": "NORMAL"
        })
        return

    # Sinkronisasi state tracker: hapus tiket yang sudah ditutup di pasar
    active_tickets = {pos.ticket for pos in positions}
    for t in list(position_states.keys()):
        if t not in active_tickets:
            del position_states[t]

    symbol_info = get_symbol_info(symbol)
    if not symbol_info:
        return

    tick = get_current_tick(symbol)
    if not tick:
        return

    metadata = get_symbol_metadata(symbol)
    digits = metadata["digits"]
    point = metadata["point"]
    stops_level_dist = metadata["stops_level_dist"]
    is_gold = "XAU" in symbol.upper() or "GOLD" in symbol.upper()
    is_forex = metadata["is_forex"]
    offset_price = get_bep_offset_price(metadata, settings.BEP_LOCK_OFFSET_PRICE)

    # Deteksi mata uang akun MT5 secara dinamis
    account = mt5.account_info()
    is_idr_account = False
    if account and getattr(account, "currency", "").upper() == "IDR":
        is_idr_account = True

    current_time = time.time()

    for pos in positions:
        ticket = pos.ticket
        pos_type = pos.type # 0 = BUY, 1 = SELL
        current_market_price = tick.bid if pos_type == mt5.ORDER_TYPE_BUY else tick.ask

        # 1. Daftarkan posisi baru ke dalam state tracker jika belum tercatat
        if ticket not in position_states:
            is_bep_pre = False
            # Validasi BEP murni: SL harus benar-benar sudah berada di atas harga entry untuk BUY
            if pos_type == mt5.ORDER_TYPE_BUY and pos.sl >= round(pos.price_open + (offset_price * 0.5), digits):
                is_bep_pre = True
            # SL harus benar-benar sudah berada di bawah harga entry untuk SELL
            elif pos_type == mt5.ORDER_TYPE_SELL and pos.sl > 0.0 and pos.sl <= round(pos.price_open - (offset_price * 0.5), digits):
                is_bep_pre = True

            position_states[ticket] = {
                "entry_price": float(pos.price_open),
                "initial_sl": float(pos.sl),
                "initial_tp": float(pos.tp),
                "extreme_price": float(current_market_price),
                "last_extreme_time": current_time,
                "bep_done": is_bep_pre,
                "stagnant_locked": False,
                "tp_extended": False
            }

        state = position_states[ticket]
        entry_price = state["entry_price"]
        initial_tp = state["initial_tp"]
        target_distance = abs(initial_tp - entry_price) if initial_tp > 0 else 0.0

        # 2. Perhitungan Jarak & Rekor Ekstrem Harga
        if pos_type == mt5.ORDER_TYPE_BUY:
            if tick.bid > state["extreme_price"]:
                state["extreme_price"] = tick.bid
                state["last_extreme_time"] = current_time
            peak_distance = max(0.0, state["extreme_price"] - entry_price)
            current_distance = max(0.0, tick.bid - entry_price)
        else: # SELL
            if tick.ask < state["extreme_price"]:
                state["extreme_price"] = tick.ask
                state["last_extreme_time"] = current_time
            peak_distance = max(0.0, entry_price - state["extreme_price"])
            current_distance = max(0.0, entry_price - tick.ask)

        # -----------------------------------------------------------------
        # TAHAP 1: Standard Auto BEP
        # -----------------------------------------------------------------
        if settings.ENABLE_AUTO_BEP and not state["bep_done"]:
            if is_idr_account:
                floating_profit_idr = pos.profit
                profit_display_str = f"Rp{floating_profit_idr:,.0f}"
            else:
                floating_profit_idr = pos.profit * float(getattr(settings, "KURS_USD_IDR", 16000.0))
                profit_display_str = f"Rp{floating_profit_idr:,.0f} (${pos.profit:.2f})"

            if floating_profit_idr >= settings.BEP_TRIGGER_PROFIT_IDR:
                if pos_type == mt5.ORDER_TYPE_BUY:
                    target_sl = round(entry_price + offset_price, digits)
                    if (tick.bid - target_sl) >= stops_level_dist and pos.sl < target_sl:
                        res = modify_position_sltp(ticket, pos.symbol, target_sl, pos.tp)
                        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                            state["bep_done"] = True
                            logger.info(
                                f"[BEP TRIGGERED] Tiket {ticket} berhasil diproteksi! "
                                f"Floating Profit: {profit_display_str} | "
                                f"SL digeser ke {target_sl:.{digits}f} (Locked Profit)."
                            )
                            add_dashboard_event("BEP", f"Tiket {ticket} SL digeser ke {target_sl:.{digits}f} (Auto BEP)")
                elif pos_type == mt5.ORDER_TYPE_SELL:
                    target_sl = round(entry_price - offset_price, digits)
                    if (target_sl - tick.ask) >= stops_level_dist and (pos.sl > target_sl or pos.sl == 0.0):
                        res = modify_position_sltp(ticket, pos.symbol, target_sl, pos.tp)
                        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                            state["bep_done"] = True
                            logger.info(
                                f"[BEP TRIGGERED] Tiket {ticket} berhasil diproteksi! "
                                f"Floating Profit: {profit_display_str} | "
                                f"SL digeser ke {target_sl:.{digits}f} (Locked Profit)."
                            )
                            add_dashboard_event("BEP", f"Tiket {ticket} SL digeser ke {target_sl:.{digits}f} (Auto BEP)")

        # -----------------------------------------------------------------
        # TAHAP 2: Stagnant Profit Lock
        # -----------------------------------------------------------------
        if settings.ENABLE_DYNAMIC_MANAGEMENT and state["bep_done"] and not state["stagnant_locked"] and target_distance > 0:
            trigger_dist = target_distance * settings.STAGNANT_LOCK_TRIGGER_RATIO
            if peak_distance >= trigger_dist:
                time_elapsed = current_time - state["last_extreme_time"]
                if time_elapsed >= settings.STAGNANT_TIMEOUT_SECONDS:
                    if pos_type == mt5.ORDER_TYPE_BUY:
                        candidate_sl = round(entry_price + (peak_distance * settings.STAGNANT_LOCK_PROFIT_RATIO), digits)
                        if candidate_sl > pos.sl and (tick.bid - candidate_sl) >= stops_level_dist:
                            res = modify_position_sltp(ticket, pos.symbol, candidate_sl, pos.tp)
                            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                state["stagnant_locked"] = True
                                logger.info(
                                    f"[STAGNANT LOCK] Posisi tertahan selama {int(time_elapsed)}s. "
                                    f"SL berhasil dinaikkan ke {candidate_sl:.{digits}f} untuk mengunci profit!"
                                )
                                add_dashboard_event("BEP", f"Tiket {ticket} SL dinaikkan ke {candidate_sl:.{digits}f} (Stagnant Lock)")
                    elif pos_type == mt5.ORDER_TYPE_SELL:
                        candidate_sl = round(entry_price - (peak_distance * settings.STAGNANT_LOCK_PROFIT_RATIO), digits)
                        if (pos.sl == 0.0 or candidate_sl < pos.sl) and (candidate_sl - tick.ask) >= stops_level_dist:
                            res = modify_position_sltp(ticket, pos.symbol, candidate_sl, pos.tp)
                            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                state["stagnant_locked"] = True
                                logger.info(
                                    f"[STAGNANT LOCK] Posisi tertahan selama {int(time_elapsed)}s. "
                                    f"SL berhasil diturunkan ke {candidate_sl:.{digits}f} untuk mengunci profit!"
                                )
                                add_dashboard_event("BEP", f"Tiket {ticket} SL diturunkan ke {candidate_sl:.{digits}f} (Stagnant Lock)")

        # -----------------------------------------------------------------
        # TAHAP 3: Dynamic TP Extender / Runner
        # -----------------------------------------------------------------
        if settings.ENABLE_DYNAMIC_MANAGEMENT and settings.ENABLE_TP_EXTENDER and not state["tp_extended"] and target_distance > 0:
            trigger_dist = target_distance * settings.TP_EXTENDER_TRIGGER_RATIO
            if current_distance >= trigger_dist:
                min_rr = getattr(settings, "MIN_RR_RATIO", 1.5)
                initial_sl_dist = abs(entry_price - state["initial_sl"]) if state["initial_sl"] > 0 else (target_distance / min_rr)
                if is_gold:
                    extension = round(max(1.50, initial_sl_dist * 0.5) * settings.TP_EXTEND_ATR_MULT, digits)
                elif is_forex:
                    extension = round(max(50.0 * point, initial_sl_dist * 0.5) * settings.TP_EXTEND_ATR_MULT, digits)
                else:
                    extension = round(1.50 * settings.TP_EXTEND_ATR_MULT, digits)
                extension = max(extension, stops_level_dist)

                # Kunci SL minimal ke rasio 1:1.2 dari initial SL risk
                min_lock_sl_dist = max(initial_sl_dist * 1.2, target_distance * settings.TP_EXTEND_SL_LOCK_RATIO)

                if pos_type == mt5.ORDER_TYPE_BUY:
                    new_tp = round(initial_tp + extension, digits)
                    new_sl = round(entry_price + min_lock_sl_dist, digits)
                    if new_sl > pos.sl and (tick.bid - new_sl) >= stops_level_dist and (new_tp - tick.bid) >= stops_level_dist:
                        res = modify_position_sltp(ticket, pos.symbol, new_sl, new_tp)
                        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                            state["tp_extended"] = True
                            state["bep_done"] = True
                            state["stagnant_locked"] = True
                            logger.info(
                                f"[TP EXTENDER] Momentum kencang! Target TP diperpanjang ke {new_tp:.{digits}f} "
                                f"dan SL dikerek ke {new_sl:.{digits}f} (Lock profit rasio >= 1:1.2)."
                            )
                            add_dashboard_event("TP", f"Tiket {ticket} TP diperpanjang ke {new_tp:.{digits}f}, SL ke {new_sl:.{digits}f}")
                elif pos_type == mt5.ORDER_TYPE_SELL:
                    new_tp = round(initial_tp - extension, digits)
                    new_sl = round(entry_price - min_lock_sl_dist, digits)
                    if (pos.sl == 0.0 or new_sl < pos.sl) and (new_sl - tick.ask) >= stops_level_dist and (tick.ask - new_tp) >= stops_level_dist:
                        res = modify_position_sltp(ticket, pos.symbol, new_sl, new_tp)
                        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                            state["tp_extended"] = True
                            state["bep_done"] = True
                            state["stagnant_locked"] = True
                            logger.info(
                                f"[TP EXTENDER] Momentum kencang! Target TP diperpanjang ke {new_tp:.{digits}f} "
                                f"dan SL dikerek ke {new_sl:.{digits}f} (Lock profit rasio >= 1:1.2)."
                            )
                            add_dashboard_event("TP", f"Tiket {ticket} TP diperpanjang ke {new_tp:.{digits}f}, SL ke {new_sl:.{digits}f}")

        # Update status posisi berjalan ke dashboard state
        stage_label = "NORMAL"
        if state.get("tp_extended"):
            stage_label = "EXTENDER"
        elif state.get("stagnant_locked"):
            stage_label = "STAGNANT"
        elif state.get("bep_done"):
            stage_label = "BEP"

        update_dashboard_state("active_position", {
            "has_position": True,
            "ticket": int(pos.ticket),
            "type": "BUY" if pos_type == mt5.ORDER_TYPE_BUY else "SELL",
            "lot": float(pos.volume),
            "open_price": float(pos.price_open),
            "current_sl": float(pos.sl),
            "current_tp": float(pos.tp),
            "floating_pnl_idr": float(floating_profit_idr),
            "stepped_stage": stage_label
        })

def check_and_apply_bep(symbol: str) -> None:
    """Legacy alias untuk pengawalan posisi terbuka bertingkat."""
    manage_open_positions(symbol)

def get_position_state(ticket: int) -> dict:
    """Mengambil state tracker posisi dari memori lokal jika ada."""
    return position_states.get(ticket, {})

def apply_ai_position_modification(
    symbol: str,
    ticket: int,
    new_sl: float,
    new_tp: float,
    action_type: str = "MODIFY"
) -> bool:
    """
    Menerapkan perubahan SL/TP taktis berdasarkan keputusan AI dengan guardrail:
    1. Validasi SL tidak boleh memperlebar risiko dari SL saat ini.
    2. Validasi jarak new_sl terhadap harga pasar mematuhi batasan minimal stops_level broker (menghindari error 10016).
    3. Eksekusi mt5.TRADE_ACTION_SLTP jika valid.
    """
    new_sl = float(new_sl)
    new_tp = float(new_tp)
    action_type = str(action_type)

    positions = mt5.positions_get(ticket=ticket)
    if positions is None or len(positions) == 0:
        logger.info(f"[INFO] Posisi tiket {ticket} sudah tidak aktif di terminal (tidak dapat dimodifikasi).")
        return False

    pos = positions[0]
    pos_type = pos.type # 0 = BUY, 1 = SELL
    price_open = pos.price_open
    old_sl = pos.sl
    old_tp = pos.tp

    metadata = get_symbol_metadata(symbol)
    digits = metadata["digits"]
    stops_level_dist = metadata["stops_level_dist"]

    tick = get_current_tick(symbol)
    if not tick:
        logger.warning(f"[WARNING] Gagal mengambil harga tick terkini untuk modifikasi tiket {ticket}.")
        return False

    # Jika new_sl tidak diubah atau <= 0, pertahankan old_sl
    if new_sl <= 0.0:
        new_sl = old_sl

    # Jika new_tp tidak diubah atau <= 0, pertahankan old_tp
    if new_tp <= 0.0:
        new_tp = old_tp

    # 1. Guardrail: Validasi SL tidak boleh memperlebar risiko dari SL saat ini
    if pos_type == mt5.ORDER_TYPE_BUY:
        if old_sl > 0.0 and new_sl < old_sl:
            logger.warning(
                f"[GUARDRAIL BLOCKED] AI mencoba memperlebar risiko BUY pada tiket {ticket} "
                f"(SL baru {new_sl:.{digits}f} < SL lama {old_sl:.{digits}f}). Ditolak."
            )
            return False
        if (tick.bid - new_sl) < stops_level_dist:
            logger.warning(
                f"[GUARDRAIL BLOCKED] Jarak SL baru {new_sl:.{digits}f} terlalu dekat atau melebihi Bid {tick.bid:.{digits}f}. Ditolak."
            )
            return False
        if new_tp > 0.0 and (new_tp - tick.bid) < stops_level_dist:
            logger.warning(
                f"[GUARDRAIL BLOCKED] Jarak TP baru {new_tp:.{digits}f} terlalu dekat dengan Bid {tick.bid:.{digits}f}. Ditolak."
            )
            return False

    elif pos_type == mt5.ORDER_TYPE_SELL:
        if old_sl > 0.0 and new_sl > old_sl:
            logger.warning(
                f"[GUARDRAIL BLOCKED] AI mencoba memperlebar risiko SELL pada tiket {ticket} "
                f"(SL baru {new_sl:.{digits}f} > SL lama {old_sl:.{digits}f}). Ditolak."
            )
            return False
        if (new_sl - tick.ask) < stops_level_dist:
            logger.warning(
                f"[GUARDRAIL BLOCKED] Jarak SL baru {new_sl:.{digits}f} terlalu dekat atau lebih rendah dari Ask {tick.ask:.{digits}f}. Ditolak."
            )
            return False
        if new_tp > 0.0 and (tick.ask - new_tp) < stops_level_dist:
            logger.warning(
                f"[GUARDRAIL BLOCKED] Jarak TP baru {new_tp:.{digits}f} terlalu dekat dengan Ask {tick.ask:.{digits}f}. Ditolak."
            )
            return False

    # 2. Guardrail Rasio R:R untuk new_tp (tidak boleh menurunkan R:R < MIN_RR_RATIO kecuali posisi sudah trailing lock)
    if new_tp != old_tp and new_tp > 0.0:
        sl_for_rr = abs(price_open - new_sl) if new_sl > 0 else abs(price_open - old_sl)
        tp_for_rr = abs(new_tp - price_open)
        is_trailing_lock = (pos_type == mt5.ORDER_TYPE_BUY and new_sl > price_open) or (pos_type == mt5.ORDER_TYPE_SELL and new_sl < price_open)
        min_rr = getattr(settings, "MIN_RR_RATIO", 1.5)
        if not is_trailing_lock and sl_for_rr > 0:
            new_rr = tp_for_rr / sl_for_rr
            if new_rr < min_rr:
                logger.warning(
                    f"[GUARDRAIL BLOCKED] AI menyarankan new_tp yang membuat R:R turun di bawah 1:{min_rr:.1f} "
                    f"(R:R: 1:{new_rr:.2f}). Ditolak."
                )
                return False

    # 3. Anti-Spam: Cek apakah nilai SL dan TP sudah identik
    new_sl = round(new_sl, digits)
    new_tp = round(new_tp, digits)
    if abs(new_sl - old_sl) < (metadata["point"] / 2.0) and abs(new_tp - old_tp) < (metadata["point"] / 2.0):
        logger.info(f"[AI POSITION MODIFY] Nilai SL/TP tiket {ticket} sudah identik ({new_sl:.{digits}f}/{new_tp:.{digits}f}). Modifikasi dilewati.")
        return True

    # 4. Eksekusi modifikasi ke server MT5
    res = modify_position_sltp(ticket, pos.symbol, new_sl, new_tp)
    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
        if ticket in position_states:
            position_states[ticket]["initial_sl"] = new_sl
            position_states[ticket]["initial_tp"] = new_tp
        logger.info(
            f"[AI POSITION MODIFY] Tiket {ticket} berhasil dimodifikasi taktis! "
            f"SL: {old_sl:.{digits}f} -> {new_sl:.{digits}f} | "
            f"TP: {old_tp:.{digits}f} -> {new_tp:.{digits}f}."
        )
        return True
    else:
        err_msg = f"Retcode: {res.retcode} - {res.comment}" if res else "No response"
        logger.warning(f"[WARNING] Gagal menerapkan modifikasi AI untuk tiket {ticket}: {err_msg}")
        return False

# ==============================================================================
# State Management Trigger Plan (AI Commander & Python Sniper)
# ==============================================================================
active_trigger_plans = []

def set_trigger_plan(
    symbol: str,
    action: str,
    trigger_price: float,
    sl_price: float,
    tp_price: float,
    confidence: float,
    expire_seconds: float = 60.0
):
    """Menyimpan satu rencana trigger breakout / pullback aktif ke memori lokal."""
    global active_trigger_plans
    norm_action = 'BUY' if 'BUY' in str(action).upper() else 'SELL'
    plan = {
        "symbol": symbol,
        "action": norm_action, # 'BUY' atau 'SELL'
        "original_action": str(action).upper(),
        "trigger_price": float(trigger_price),
        "sl_price": float(sl_price),
        "tp_price": float(tp_price),
        "created_at": time.time(),
        "expire_seconds": float(expire_seconds),
        "confidence": float(confidence)
    }
    active_trigger_plans = [plan]
    update_dashboard_state("active_trigger", {
        "has_plan": True,
        "action": plan["original_action"],
        "trigger_price": float(plan["trigger_price"]),
        "planned_sl": float(plan["sl_price"]),
        "planned_tp": float(plan["tp_price"]),
        "expires_in_sec": int(plan["expire_seconds"])
    })

def set_straddle_trigger_plans(plans: list):
    """Menyimpan daftar rencana trigger jebakan ganda (Straddle / Multiple Triggers)."""
    global active_trigger_plans
    active_trigger_plans = list(plans) if plans else []
    if active_trigger_plans:
        p0 = active_trigger_plans[0]
        act_label = "STRADDLE TRAP" if len(active_trigger_plans) > 1 else p0["original_action"]
        update_dashboard_state("active_trigger", {
            "has_plan": True,
            "action": act_label,
            "trigger_price": float(p0["trigger_price"]),
            "planned_sl": float(p0["sl_price"]),
            "planned_tp": float(p0["tp_price"]),
            "expires_in_sec": int(p0["expire_seconds"])
        })
    else:
        update_dashboard_state("active_trigger", {
            "has_plan": False,
            "action": "-",
            "trigger_price": 0.0,
            "planned_sl": 0.0,
            "planned_tp": 0.0,
            "expires_in_sec": 0
        })

def get_trigger_plan():
    """Mengambil data trigger plan pertama (kompatibilitas skrip/test lama)."""
    return active_trigger_plans[0] if active_trigger_plans else None

def get_trigger_plans():
    """Mengambil seluruh daftar active_trigger_plans saat ini."""
    return list(active_trigger_plans)

def reset_trigger_plan():
    """Membatalkan / mereset seluruh active_trigger_plans."""
    global active_trigger_plans
    active_trigger_plans = []
    update_dashboard_state("active_trigger", {
        "has_plan": False,
        "action": "-",
        "trigger_price": 0.0,
        "planned_sl": 0.0,
        "planned_tp": 0.0,
        "expires_in_sec": 0
    })

def create_straddle_plans(symbol: str, mtf_data: dict, tick: Any, bias: str = "BOTH") -> list:
    """
    Membuat rencana trap breakout mekanis (Straddle / Directional Trap):
    - bias == "BOTH"    : Pasang BUY trap dan SELL trap sekaligus (Straddle Trap & Reverse Non-AI).
    - bias == "BULLISH" : Pasang HANYA BUY trap searah tren (Hybrid AI-Straddle).
    - bias == "BEARISH" : Pasang HANYA SELL trap searah tren (Hybrid AI-Straddle).
    - bias == "NEUTRAL" : Tidak pasang trap (HOLD) demi menghindari resiko sideways.
    """
    if str(bias).upper() == "NEUTRAL":
        return []

    metadata = mtf_data.get("metadata", {})
    digits = metadata.get("digits", 2)
    point = float(metadata.get("point", 0.01))
    stops_level_dist = float(metadata.get("stops_level_dist", 30 * point))
    is_gold = "XAU" in symbol.upper() or "GOLD" in symbol.upper()

    hierarchy = mtf_data.get("hierarchy", {})
    base_tf_str = hierarchy.get("base", "M1").lower()
    base_data = mtf_data.get("base") or mtf_data.get(base_tf_str, {})
    recent_candles = base_data.get("recent_candles", [])

    if not recent_candles:
        return []

    last_candle = recent_candles[-1]
    high_ref = float(last_candle.get("high", tick.ask))
    low_ref = float(last_candle.get("low", tick.bid))
    base_atr = float(base_data.get("atr", 1.20 if is_gold else 0.0003))

    min_trigger_buffer = max(0.25 if is_gold else 25 * point, stops_level_dist)
    min_rr = getattr(settings, "MIN_RR_RATIO", 1.5)
    sl_cap = getattr(settings, "SL_CAP_PRICE", 2.50 if is_gold else 0.0025)

    if is_gold:
        sl_dist = 2.00
    elif metadata.get("is_forex", False):
        sl_dist = round(50.0 * point, digits)
    else:
        sl_dist = round(1.5 * base_atr, digits)

    sl_dist = min(sl_dist, sl_cap)
    tp_dist = round(sl_dist * min_rr, digits)

    plans = []
    current_time = time.time()
    bias_norm = str(bias).upper()

    # 1. Rencana BUY Trap
    if bias_norm in ("BOTH", "BULLISH"):
        buy_trigger = round(max(tick.ask + min_trigger_buffer, high_ref + point), digits)
        buy_sl = round(buy_trigger - sl_dist, digits)
        buy_tp = round(buy_trigger + tp_dist, digits)
        plans.append({
            "symbol": symbol,
            "action": "BUY",
            "original_action": "PENDING_BUY",
            "trigger_price": buy_trigger,
            "sl_price": buy_sl,
            "tp_price": buy_tp,
            "created_at": current_time,
            "expire_seconds": 60.0,
            "confidence": 0.88 if bias_norm == "BULLISH" else 0.80
        })

    # 2. Rencana SELL Trap
    if bias_norm in ("BOTH", "BEARISH"):
        sell_trigger = round(min(tick.bid - min_trigger_buffer, low_ref - point), digits)
        sell_sl = round(sell_trigger + sl_dist, digits)
        sell_tp = round(sell_trigger - tp_dist, digits)
        plans.append({
            "symbol": symbol,
            "action": "SELL",
            "original_action": "PENDING_SELL",
            "trigger_price": sell_trigger,
            "sl_price": sell_sl,
            "tp_price": sell_tp,
            "created_at": current_time,
            "expire_seconds": 60.0,
            "confidence": 0.88 if bias_norm == "BEARISH" else 0.80
        })

    return plans

def check_and_execute_trigger_plan(symbol: str, execution_mode: str = "AUTO") -> bool:
    """
    Dipanggil setiap detik di perulangan utama main.py (Jalur Penembak Jitu):
    1. Cek apakah ada active_trigger_plans. Bersihkan plan yang expired.
    2. Cek apakah bot sudah memiliki posisi terbuka (max positions) atau circuit breaker locked.
    3. Ambil tick harga terkini:
       - Jika salah satu plan terpicu (BUY / SELL breakout):
           Langsung eksekusi, bersihkan seluruh sisa plan (anti-double entry).
    """
    global active_trigger_plans
    if not active_trigger_plans:
        return False

    current_time = time.time()

    # 1. Bersihkan plan yang sudah kedaluwarsa (> expire_seconds)
    active_trigger_plans = [
        p for p in active_trigger_plans
        if (current_time - p["created_at"]) <= p["expire_seconds"]
    ]
    if not active_trigger_plans:
        update_dashboard_state("active_trigger", {
            "has_plan": False,
            "action": "-",
            "trigger_price": 0.0,
            "planned_sl": 0.0,
            "planned_tp": 0.0,
            "expires_in_sec": 0
        })
        return False

    # Update sisa waktu hitung mundur (countdown) di dashboard
    p0 = active_trigger_plans[0]
    rem_sec = int(max(0, p0["expire_seconds"] - (current_time - p0["created_at"])))
    act_label = "STRADDLE TRAP" if len(active_trigger_plans) > 1 else p0["original_action"]
    update_dashboard_state("active_trigger", {
        "has_plan": True,
        "action": act_label,
        "trigger_price": float(p0["trigger_price"]),
        "planned_sl": float(p0["sl_price"]),
        "planned_tp": float(p0["tp_price"]),
        "expires_in_sec": rem_sec
    })

    # 2. Cek apakah circuit breaker sedang terkunci
    is_locked, _ = is_circuit_breaker_locked()
    if is_locked:
        reset_trigger_plan()
        return False

    # 3. Cek apakah bot sudah memiliki posisi terbuka maksimum
    active_pos = get_active_positions(symbol)
    if len(active_pos) >= settings.MAX_OPEN_POSITIONS:
        reset_trigger_plan()
        return False

    # 4. Ambil tick pasar real-time
    tick = get_current_tick(symbol)
    if not tick:
        return False

    metadata = get_symbol_metadata(symbol)
    digits = metadata["digits"]

    for plan in list(active_trigger_plans):
        fired = False
        order_type = None

        if plan["action"] == "BUY" and tick.ask >= plan["trigger_price"]:
            fired = True
            order_type = mt5.ORDER_TYPE_BUY
        elif plan["action"] == "SELL" and tick.bid <= plan["trigger_price"]:
            fired = True
            order_type = mt5.ORDER_TYPE_SELL

        if fired:
            # Simpan salinan data plan yang terpicu dan langsung kosongkan seluruh trap
            saved_plan = dict(plan)
            reset_trigger_plan()

            entry_price = tick.ask if saved_plan["action"] == "BUY" else tick.bid
            sl_dist_price = abs(entry_price - saved_plan["sl_price"])

            if execution_mode == "AUTO":
                lot = calculate_lot_size(symbol, sl_dist_price=sl_dist_price)
                if lot <= 0 or sl_dist_price <= 0:
                    logger.error(f"[ERROR] Sniper Trigger dibatalkan: SL tidak valid atau lot <= 0.")
                    return False

                comment = f"Sniper_{saved_plan['action']}_{saved_plan['confidence']:.2f}"
                res = open_position(
                    symbol,
                    order_type,
                    lot,
                    sl_price=saved_plan["sl_price"],
                    tp_price=saved_plan["tp_price"],
                    comment=comment
                )
                if res:
                    logger.info(
                        f"[SNIPER TRIGGER] Harga menyentuh trigger {saved_plan['trigger_price']:.{digits}f} "
                        f"(Market: {entry_price:.{digits}f})! Eksekusi {saved_plan['action']} instan berhasil! Tiket: {res.order}"
                    )
                    add_dashboard_event("ORDER", f"Sniper {saved_plan['action']} terpicu @ {entry_price:.{digits}f} (Tiket: {res.order})")
                    return True
                else:
                    logger.warning(f"[WARNING] Sniper Trigger gagal dieksekusi di server broker.")
                    return False
            else:
                # Mode SIGNAL ONLY
                logger.info(
                    f"[SNIPER TRIGGER SIGNAL] Harga pasar ({entry_price:.{digits}f}) menembus trigger {saved_plan['trigger_price']:.{digits}f}! "
                    f"Sinyal {saved_plan['action']} terpicu instan (Signal Only)."
                )
                add_dashboard_event("ORDER", f"Sniper Signal {saved_plan['action']} terpicu @ {entry_price:.{digits}f}")
                print_sniper_execution_box(
                    symbol=symbol,
                    action=saved_plan["action"],
                    trigger_price=saved_plan["trigger_price"],
                    market_price=entry_price,
                    sl_price=saved_plan["sl_price"],
                    tp_price=saved_plan["tp_price"],
                    confidence=saved_plan["confidence"],
                    digits=digits
                )
                return True

    return False

def print_sniper_execution_box(
    symbol: str,
    action: str,
    trigger_price: float,
    market_price: float,
    sl_price: float,
    tp_price: float,
    confidence: float,
    digits: int = 2
):
    """Mencetak kotak notifikasi penembak jitu saat harga menembus level trigger (Mode Signal Only)."""
    current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    min_rr = getattr(settings, "MIN_RR_RATIO", 1.5)

    sl_dist = abs(market_price - sl_price)
    min_tp_dist = sl_dist * min_rr
    if action == "BUY" and (tp_price - market_price) < min_tp_dist:
        tp_price = round(market_price + min_tp_dist, digits)
    elif action == "SELL" and (market_price - tp_price) < min_tp_dist:
        tp_price = round(market_price - min_tp_dist, digits)

    tp_dist = abs(tp_price - market_price)
    rr_ratio = round((tp_dist / sl_dist), 2) if sl_dist > 0 else min_rr

    dist_prefix = "$" if digits <= 2 else ""
    dist_dec = 2 if digits <= 2 else digits

    sl_str = f"{sl_price:.{digits}f} (Jarak: {dist_prefix}{sl_dist:.{dist_dec}f})"
    tp_str = f"{tp_price:.{digits}f} (Jarak: {dist_prefix}{tp_dist:.{dist_dec}f})"
    rr_str = f"1:{rr_ratio:.2f}"
    entry_str = f"{market_price:.{digits}f}"
    trig_str = f"{trigger_price:.{digits}f}"
    conf_str = f"{confidence * 100:.1f}%"
    action_str = f"{action} (EKSEKUSI MANUAL!)"

    content_width = 46

    box_lines = [
        "╔══════════════════════════════════════════════════════════════╗",
        "║              🎯 SNIPER TRIGGER TERPICU (SIGNAL) 🎯            ║",
        "╠══════════════════════════════════════════════════════════════╣",
        f"║ Instrumen   : {symbol:<{content_width}} ║",
        f"║ Aksi        : {action_str:<{content_width}} ║",
        f"║ Harga Pemicu: {trig_str:<{content_width}} ║",
        f"║ Harga Riil  : {entry_str:<{content_width}} ║",
        f"║ Stop Loss   : {sl_str:<{content_width}} ║",
        f"║ Take Profit : {tp_str:<{content_width}} ║",
        f"║ Rasio R:R   : {rr_str:<{content_width}} ║",
        f"║ Confidence  : {conf_str:<{content_width}} ║",
        f"║ Waktu       : {f'{current_timestamp} WIB':<{content_width}} ║",
        "╚══════════════════════════════════════════════════════════════╝"
    ]

    box_str = "\n" + "\n".join(box_lines) + "\n"
    try:
        print(box_str)
    except UnicodeEncodeError:
        ascii_box = box_str.replace("╔", "+").replace("╗", "+").replace("╚", "+").replace("╝", "+")
        ascii_box = ascii_box.replace("╠", "+").replace("╣", "+").replace("═", "-").replace("║", "|")
        ascii_box = ascii_box.replace("🎯", "!")
        print(ascii_box)