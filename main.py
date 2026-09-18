import argparse
import os
import sys
from dotenv import load_dotenv

# Bootstrap Multi-Instance CLI Argument
parser = argparse.ArgumentParser(description="Multi-Instance MT5 Scalper Bot")
parser.add_argument(
    "--env", 
    type=str, 
    default=".env", 
    help="Path file konfigurasi env spesifik (misal: .env.xau atau .env.forex)"
)
args = parser.parse_args()

# Muat file env yang dipilih dengan override=True sebelum modul lain diimpor
if os.path.exists(args.env):
    load_dotenv(args.env, override=True)
    print(f"[BOOTSTRAP] Memuat konfigurasi dari: {args.env}")
else:
    print(f"[BOOTSTRAP WARNING] File {args.env} tidak ditemukan, menggunakan environment default.")

import time
import textwrap
from datetime import datetime
import MetaTrader5 as mt5
from config import settings
from utils.logger import logger
from src.data.mt5_connection import initialize_mt5, shutdown_mt5
from src.data.market_data import (
    get_historical_data,
    is_market_open,
    get_symbol_info,
    get_current_tick,
    get_multi_timeframe_data
)
from src.strategy.ai_strategy import get_ai_decision
from src.execution.order_manager import (
    calculate_lot_size,
    open_position,
    close_position,
    get_active_positions,
    check_daily_drawdown_limit,
    check_and_apply_bep,
    manage_open_positions,
    apply_ai_position_modification,
    evaluate_daily_pnl_limits,
    set_circuit_breaker_lock,
    get_daily_realized_pnl,
    set_trigger_plan,
    set_straddle_trigger_plans,
    create_straddle_plans,
    reset_trigger_plan,
    check_and_execute_trigger_plan
)

TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": getattr(mt5, "TIMEFRAME_M30", 30),
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

def print_signal_box(
    symbol: str,
    action: str,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    sl_dist: float,
    tp_dist: float,
    rr_ratio: float,
    confidence: float,
    reason: str,
    digits: int = 2,
    base_tf: str = "M1",
    primary_tf: str = "M5",
    primary_trend: str = "NEUTRAL",
    macro_tf: str = "M15",
    macro_trend: str = "NEUTRAL"
):
    """Mencetak kotak sinyal rekomendasi AI yang rapi dan mencolok di terminal (Mode Signal Only)."""
    current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conf_str = f"{confidence * 100:.1f}%"
    is_gold = "XAU" in symbol.upper() or "GOLD" in symbol.upper()
    if is_gold:
        sl_dist_str = f"${sl_dist:.2f}"
        tp_dist_str = f"${tp_dist:.2f}"
    else:
        pip_size = 10 ** (1 - digits) if digits > 2 else 0.0001
        sl_pips = sl_dist / pip_size
        tp_pips = tp_dist / pip_size
        sl_dist_str = f"{sl_pips:.1f} pips"
        tp_dist_str = f"{tp_pips:.1f} pips"

    sl_str = f"{sl_price:.{digits}f} (Jarak: {sl_dist_str})"
    tp_str = f"{tp_price:.{digits}f} (Jarak: {tp_dist_str})"
    rr_str = f"1:{rr_ratio:.2f} (Min. 1:1.50)"
    entry_str = f"{entry_price:.{digits}f}"
    inst_str = f"{symbol} (Base TF: {base_tf})"
    primary_ctx_str = f"Primary {primary_tf}: {primary_trend}"
    macro_ctx_str = f"Macro   {macro_tf}: {macro_trend} (Ref Only)"
    
    val_width = 46
    wrapped_reason = textwrap.wrap(reason, width=val_width) or ["-"]
    
    box_lines = [
        "╔══════════════════════════════════════════════════════════════╗",
        "║                   🚨 REKOMENDASI SINYAL AI 🚨                 ║",
        "╠══════════════════════════════════════════════════════════════╣",
        f"║ Instrumen   : {inst_str:<{val_width}} ║",
        f"║ Aksi        : {action:<{val_width}} ║",
        f"║ Entry Saat  : {entry_str:<{val_width}} ║",
        f"║ Stop Loss   : {sl_str:<{val_width}} ║",
        f"║ Take Profit : {tp_str:<{val_width}} ║",
        f"║ Rasio R:R   : {rr_str:<{val_width}} ║",
        f"║ Confidence  : {conf_str:<{val_width}} ║",
        f"║ Konteks MTF : {primary_ctx_str:<{val_width}} ║",
        f"║               {macro_ctx_str:<{val_width}} ║",
        f"║ Alasan      : {wrapped_reason[0]:<{val_width}} ║",
    ]
    for extra_line in wrapped_reason[1:3]:
        box_lines.append(f"║               {extra_line:<{val_width}} ║")
        
    box_lines.append(f"║ Waktu       : {f'{current_timestamp} WIB':<{val_width}} ║")
    box_lines.append("╚══════════════════════════════════════════════════════════════╝")
    
    box_str = "\n" + "\n".join(box_lines) + "\n"
    try:
        print(box_str)
    except UnicodeEncodeError:
        ascii_box = box_str.replace("╔", "+").replace("╗", "+").replace("╚", "+").replace("╝", "+")
        ascii_box = ascii_box.replace("╠", "+").replace("╣", "+").replace("═", "-").replace("║", "|")
        ascii_box = ascii_box.replace("🚨", "!")
        print(ascii_box)

def main():
    logger.info(f"[START] Memulai Trading Bot | Simbol: {settings.SYMBOL} | TF: {settings.TIMEFRAME_STR}")
    
    if not initialize_mt5():
        logger.error("[ERROR] Gagal terhubung ke MT5. Keluar.")
        return
        
    symbol = settings.SYMBOL
    base_tf_str = getattr(settings, "TIMEFRAME_STR", "M1").upper()
    timeframe = TF_MAP.get(base_tf_str, mt5.TIMEFRAME_M1)
    
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
    
    # Penentuan Mode Operasional (Auto Trading vs Signal Only)
    execution_mode = "AUTO"
    bot_mode_cfg = getattr(settings, "BOT_MODE", "INTERACTIVE").upper()

    if bot_mode_cfg == "AUTO":
        execution_mode = "AUTO"
    elif bot_mode_cfg == "SIGNAL":
        execution_mode = "SIGNAL"
    else:  # INTERACTIVE
        print("\n" + "=" * 50)
        print("           PILIH MODE OPERASIONAL BOT")
        print("=" * 50)
        print("[1] Auto Trading  : Eksekusi otomatis langsung ke MT5")
        print("[2] Signal Only   : Analisa & Rekomendasi Sinyal saja (Dry Run)")
        print("=" * 50)
        try:
            choice = input("Pilih mode (1/2) [Default: 1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            choice = "1"

        if choice == "2":
            execution_mode = "SIGNAL"
        else:
            execution_mode = "AUTO"

    # Penentuan Submenu Strategi Trading (Mode Auto Trading)
    selected_strategy = 1
    strategy_name = "AI Commander Pure"

    if execution_mode == "AUTO":
        if bot_mode_cfg == "INTERACTIVE":
            print("\n" + "=" * 50)
            print("           PILIH STRATEGI TRADING (AUTO)")
            print("=" * 50)
            print("[1] AI Commander Pure")
            print("    • Analisis MTF adaptif + Sniper Breakout Trap searah tren.")
            print("[2] Straddle Trap & Reverse")
            print("    • Pasang Buy Stop & Sell Stop mekanis menjepit harga (Non-AI).")
            print("    * Perhatian: Risiko tinggi saat pasar sideways!")
            print("[3] Hybrid AI-Straddle (Rekomendasi)")
            print("    • AI menentukan bias Primary TF, trap HANYA dipasang searah tren.")
            print("=" * 50)
            try:
                strategy_choice = input("Pilih strategi (1/2/3) [Default: 1]: ").strip() or "1"
            except (EOFError, KeyboardInterrupt):
                strategy_choice = "1"
        else:
            strategy_choice = os.getenv("STRATEGY_CHOICE", "1").strip() or "1"

        try:
            selected_strategy = int(strategy_choice)
        except ValueError:
            selected_strategy = 1

        if selected_strategy == 2:
            strategy_name = "Straddle Trap & Reverse"
        elif selected_strategy == 3:
            strategy_name = "Hybrid AI-Straddle (Rekomendasi)"
        else:
            selected_strategy = 1
            strategy_name = "AI Commander Pure"

    if execution_mode == "SIGNAL":
        logger.info("[MODE AKTIF] Bot berjalan dalam mode: SIGNAL ONLY (Tidak ada eksekusi order ke broker).")
    else:
        logger.info("[MODE AKTIF] Bot berjalan dalam mode: AUTO TRADING (Full Execution).")
        logger.info(f"[STRATEGY ACTIVE] Menjalankan strategi mode: {strategy_name}")

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

    # Inisialisasi Web Dashboard di background thread (Zero Latency Impact)
    if getattr(settings, "ENABLE_WEB_DASHBOARD", True):
        try:
            from src.web.server import start_dashboard_thread, update_dashboard_state
            web_port = getattr(settings, "WEB_PORT", 8080)
            start_dashboard_thread(port=web_port)
            
            is_gold_inst = "XAU" in symbol.upper() or "GOLD" in symbol.upper()
            inst_type = "GOLD" if is_gold_inst else "FOREX"
            inst_label = f"INSTANCE: {inst_type} ({symbol})"
            sym_digits = info.digits if info else (2 if is_gold_inst else 5)
            sym_point = info.point if info else (0.01 if is_gold_inst else 0.00001)

            update_dashboard_state("instance", {
                "symbol": symbol,
                "type": inst_type,
                "label": inst_label,
                "port": web_port
            })
            update_dashboard_state("market", {
                "symbol": symbol,
                "timeframe": base_tf_str,
                "digits": sym_digits,
                "point": sym_point,
                "is_forex": not is_gold_inst,
                "is_connected": True
            })
            if acc_init:
                update_dashboard_state("account", {
                    "balance": float(acc_init.balance),
                    "equity": float(acc_init.equity),
                    "margin_free": float(acc_init.margin_free),
                    "currency": curr_code
                })
        except Exception as e:
            logger.warning(f"[WARNING] Gagal menyalakan dashboard web: {e}")
    
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

            # Evaluasi status pasar
            market_active = is_market_open(symbol)

            # Update Snapshot Akun & Pasar ke Dashboard Web secara real-time (Non-Blocking, tetap update walau pasar tutup)
            if getattr(settings, "ENABLE_WEB_DASHBOARD", True):
                try:
                    from src.web.server import update_dashboard_state
                    acc_snap = mt5.account_info()
                    if acc_snap:
                        pnl_snap, _, _, _ = get_daily_realized_pnl(symbol)
                        cb_locked, _ = is_circuit_breaker_locked()
                        update_dashboard_state("account", {
                            "balance": float(acc_snap.balance),
                            "equity": float(acc_snap.equity),
                            "margin_free": float(acc_snap.margin_free),
                            "daily_realized_pnl": float(pnl_snap),
                            "currency": getattr(acc_snap, "currency", "USD").upper(),
                            "circuit_breaker_locked": bool(cb_locked)
                        })

                    t_snap = get_current_tick(symbol)
                    is_gold_s = "XAU" in symbol.upper() or "GOLD" in symbol.upper()
                    m_digits = info.digits if info else (2 if is_gold_s else 5)
                    m_point = info.point if info else (0.01 if is_gold_s else 0.00001)

                    if t_snap:
                        server_time_str = datetime.fromtimestamp(t_snap.time).strftime("%Y-%m-%d %H:%M:%S") if t_snap.time > 0 else "-"
                        sp_points = round((t_snap.ask - t_snap.bid) / m_point, 1) if m_point > 0 else 0.0
                        update_dashboard_state("market", {
                            "symbol": symbol,
                            "timeframe": base_tf_str,
                            "digits": m_digits,
                            "point": m_point,
                            "is_forex": not is_gold_s,
                            "current_bid": float(t_snap.bid),
                            "current_ask": float(t_snap.ask),
                            "spread_points": float(sp_points),
                            "is_connected": True,
                            "is_market_open": market_active,
                            "server_time": server_time_str
                        })
                    else:
                        update_dashboard_state("market", {
                            "symbol": symbol,
                            "timeframe": base_tf_str,
                            "digits": m_digits,
                            "point": m_point,
                            "is_forex": not is_gold_s,
                            "is_connected": True,
                            "is_market_open": market_active
                        })
                except Exception:
                    pass

            # Jika pasar tutup, tunggu 60 detik sebelum perulangan berikutnya
            if not market_active:
                logger.info("[INFO] Pasar sedang tutup. Menunggu...")
                time.sleep(60)
                continue
            
            # 1. Pemantauan Real-Time Tick (0 Latensi)
            # Jalur Pengawalan Posisi Aktif (BEP, Stagnant Lock, TP Extender)
            if execution_mode == "AUTO":
                manage_open_positions(symbol)

            # Jalur Penembak Jitu (Cek Trigger Plan Breakout real-time)
            check_and_execute_trigger_plan(symbol, execution_mode=execution_mode)

            # 2. Pelacakan & Evaluasi Daily Target Profit Lock / Circuit Breaker
            if execution_mode == "AUTO" and settings.ENABLE_DAILY_TARGET_LOCK:
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
                    # Jika masih ada posisi aktif yang berjalan, biarkan diselesaikan oleh SL/TP atau Dynamic Stepped Management
                    if execution_mode == "AUTO":
                        manage_open_positions(symbol)
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
                
                # Batalkan / reset trigger plan lama saat candle berganti
                reset_trigger_plan()
                
                # Cek Daily Drawdown Limit (Kill Switch)
                if not check_daily_drawdown_limit():
                    continue # Lewati eksekusi hingga hari berganti (script tetap jalan mengecek waktu)
                
                # 2. Ambil data Multi-Timeframe adaptif sesuai base_tf
                mtf_data = get_multi_timeframe_data(symbol, base_tf=base_tf_str)
                base_candle_data = mtf_data.get("base") or mtf_data.get(base_tf_str.lower(), {})
                if not mtf_data or not base_candle_data.get("recent_candles"):
                    continue
                    
                # 3. Cek posisi aktif dari bot ini (berdasarkan Magic Number)
                bot_positions = get_active_positions(symbol)
                has_active_pos = len(bot_positions) > 0
                
                # Ambil tick terkini
                tick = get_current_tick(symbol)
                if not tick:
                    continue
                    
                metadata = mtf_data.get("metadata", {})
                digits = metadata.get("digits", 2)
                hierarchy = mtf_data.get("hierarchy", {})
                base_tf_label = hierarchy.get("base", base_tf_str)
                primary_tf_label = hierarchy.get("primary", "M5")
                macro_tf_label = hierarchy.get("macro", "M15")

                # ==============================================================
                # PENANGANAN STRATEGI TRADING (AUTO & SIGNAL)
                # ==============================================================
                if execution_mode == "AUTO" and selected_strategy == 2:
                    # --- STRATEGI 2: Straddle Trap & Reverse (Non-AI) ---
                    current_positions = get_active_positions(symbol)
                    has_active_pos = len(current_positions) > 0

                    if getattr(settings, "ENABLE_WEB_DASHBOARD", True):
                        try:
                            from src.web.server import update_dashboard_state
                            update_dashboard_state("ai_status", {
                                "primary_tf": primary_tf_label,
                                "primary_trend": mtf_data.get("primary", {}).get("trend", "NEUTRAL"),
                                "macro_tf": macro_tf_label,
                                "macro_trend": mtf_data.get("macro", {}).get("trend", "NEUTRAL"),
                                "rsi": float(mtf_data.get("primary", {}).get("rsi", 50.0)),
                                "last_signal": "STRADDLE_TRAP",
                                "confidence": 0.85,
                                "reason_summary": "Straddle Trap mekanis (Non-AI) menjepit High/Low lilin M1."
                            })
                        except Exception:
                            pass

                    if has_active_pos:
                        logger.info(f"[STRADDLE POSITION] Memantau posisi aktif tiket {[p.ticket for p in current_positions]} via pengawalan dinamis lokal.")
                    else:
                        straddle_plans = create_straddle_plans(symbol, mtf_data, tick, bias="BOTH")
                        if straddle_plans and len(current_positions) < settings.MAX_OPEN_POSITIONS:
                            set_straddle_trigger_plans(straddle_plans)
                            logger.info(
                                f"[STRADDLE TRAP] Pasang jebakan ganda mekanis (Non-AI): "
                                f"BUY @ {straddle_plans[0]['trigger_price']:.{digits}f} (SL: {straddle_plans[0]['sl_price']:.{digits}f}, TP: {straddle_plans[0]['tp_price']:.{digits}f}) | "
                                f"SELL @ {straddle_plans[1]['trigger_price']:.{digits}f} (SL: {straddle_plans[1]['sl_price']:.{digits}f}, TP: {straddle_plans[1]['tp_price']:.{digits}f})."
                            )

                elif execution_mode == "AUTO" and selected_strategy == 3:
                    # --- STRATEGI 3: Hybrid AI-Straddle (Rekomendasi) ---
                    ai_decision = get_ai_decision(mtf_data, tick, bot_positions)
                    action = ai_decision.get('action', 'HOLD')
                    confidence = float(ai_decision.get('confidence', 0.0))
                    primary_bias = str(ai_decision.get('primary_bias', 'NEUTRAL')).upper()

                    current_positions = get_active_positions(symbol)
                    has_active_pos = len(current_positions) > 0

                    if getattr(settings, "ENABLE_WEB_DASHBOARD", True):
                        try:
                            from src.web.server import update_dashboard_state
                            update_dashboard_state("ai_status", {
                                "primary_tf": primary_tf_label,
                                "primary_trend": primary_bias,
                                "macro_tf": macro_tf_label,
                                "macro_trend": str(ai_decision.get("macro_bias", "NEUTRAL")).upper(),
                                "rsi": float(mtf_data.get("primary", {}).get("rsi", 50.0)),
                                "last_signal": action,
                                "confidence": float(confidence),
                                "reason_summary": str(ai_decision.get("summary_reason") or ai_decision.get("reason", "-")),
                                "execution_type": str(ai_decision.get("execution_type", "MARKET_ORDER")),
                                "trade_setup": ai_decision.get("trade_setup", {}),
                                "detailed_analysis": ai_decision.get("detailed_analysis", {})
                            })
                        except Exception:
                            pass

                    if has_active_pos:
                        # Re-evaluasi taktis posisi aktif oleh AI (MODIFY / CLOSE / HOLD)
                        for pos in current_positions:
                            pos_type = pos.type
                            ticket = pos.ticket
                            if action == 'MODIFY' and confidence >= settings.AI_MIN_CONFIDENCE:
                                new_sl = ai_decision.get('new_sl') or ai_decision.get('entry_sl') or ai_decision.get('sl_price', 0.0)
                                new_tp = ai_decision.get('new_tp') or ai_decision.get('entry_tp') or ai_decision.get('tp_price', 0.0)
                                apply_ai_position_modification(symbol, ticket, new_sl, new_tp, action_type="MODIFY")
                            elif (action == 'CLOSE' or (action == 'SELL' and pos_type == mt5.ORDER_TYPE_BUY) or (action == 'BUY' and pos_type == mt5.ORDER_TYPE_SELL)) and confidence >= settings.AI_MIN_CONFIDENCE:
                                logger.info(f"[REVERSAL/CLOSE] AI merekomendasikan {action}! Menutup posisi tiket {ticket}...")
                                if close_position(ticket):
                                    current_positions = get_active_positions(symbol)
                                    has_active_pos = len(current_positions) > 0
                            elif action == 'HOLD':
                                logger.info(f"[AI POSITION HOLD] Posisi tiket {ticket} dipertahankan. Alasan: {ai_decision.get('reason', '-')}")
                    else:
                        # Pasang trap HANYA searah tren Primary TF
                        if primary_bias in ("BULLISH", "BEARISH") and confidence >= settings.AI_MIN_CONFIDENCE and len(current_positions) < settings.MAX_OPEN_POSITIONS:
                            hybrid_plans = create_straddle_plans(symbol, mtf_data, tick, bias=primary_bias)
                            if hybrid_plans:
                                set_straddle_trigger_plans(hybrid_plans)
                                plan_act = hybrid_plans[0]['action']
                                plan_trig = hybrid_plans[0]['trigger_price']
                                plan_sl = hybrid_plans[0]['sl_price']
                                plan_tp = hybrid_plans[0]['tp_price']
                                logger.info(
                                    f"[HYBRID STRADDLE] Bias Primary TF ({primary_tf_label}): {primary_bias} (Conf: {confidence:.2f}). "
                                    f"Trap {plan_act} dipasang @ {plan_trig:.{digits}f} (SL: {plan_sl:.{digits}f}, TP: {plan_tp:.{digits}f})."
                                )
                        else:
                            logger.info(f"[HYBRID STRADDLE] Bias Primary TF ({primary_tf_label}): {primary_bias}. Trap tidak dipasang demi menghindari resiko sideways.")

                else:
                    # --- STRATEGI 1: AI Commander Pure (Default & Signal Mode) ---
                    # 4. Hasilkan sinyal dari strategi AI berbasis Multi-Timeframe adaptif
                    ai_decision = get_ai_decision(mtf_data, tick, bot_positions)
                    action = ai_decision.get('action', 'HOLD')
                    confidence = float(ai_decision.get('confidence', 0.0))

                    # Refresh tick harga terkini segera setelah inferensi AI selesai (mengantisipasi pergeseran harga selama 3-5 detik)
                    fresh_tick = get_current_tick(symbol)
                    if fresh_tick:
                        tick = fresh_tick

                    primary_trend = ai_decision.get("primary_bias") or mtf_data.get("primary", {}).get("trend", "NEUTRAL")
                    macro_trend = ai_decision.get("macro_bias") or mtf_data.get("macro", {}).get("trend", "NEUTRAL")

                    if getattr(settings, "ENABLE_WEB_DASHBOARD", True):
                        try:
                            from src.web.server import update_dashboard_state
                            update_dashboard_state("ai_status", {
                                "primary_tf": primary_tf_label,
                                "primary_trend": primary_trend,
                                "macro_tf": macro_tf_label,
                                "macro_trend": macro_trend,
                                "rsi": float(mtf_data.get("primary", {}).get("rsi", 50.0)),
                                "last_signal": action,
                                "confidence": float(confidence),
                                "reason_summary": str(ai_decision.get("summary_reason") or ai_decision.get("reason", "-")),
                                "execution_type": str(ai_decision.get("execution_type", "MARKET_ORDER")),
                                "trade_setup": ai_decision.get("trade_setup", {}),
                                "detailed_analysis": ai_decision.get("detailed_analysis", {})
                            })
                        except Exception:
                            pass

                    # 5. Eksekusi Berdasarkan Keputusan AI
                    if execution_mode == "SIGNAL":
                        # --- MODE 2: SIGNAL ONLY (HANYA PEMBERI SINYAL / NOTIFIKASI SAJA) ---
                        if action == 'HOLD':
                            logger.info(f"[SIGNAL ONLY] AI Rekomendasi: HOLD | Alasan: {ai_decision.get('reason', '-')}")
                        elif action in ['BUY', 'SELL'] and confidence >= settings.AI_MIN_CONFIDENCE:
                            entry_price = tick.ask if action == 'BUY' else tick.bid
                            entry_price = round(entry_price, digits)

                            sl_price = ai_decision.get('sl_price', 0.0)
                            tp_price = ai_decision.get('tp_price', 0.0)
                            min_rr = getattr(settings, "MIN_RR_RATIO", 1.5)
                            sl_dist = abs(entry_price - sl_price)
                            min_tp_dist = sl_dist * min_rr

                            if action == 'BUY' and (tp_price - entry_price) < min_tp_dist:
                                tp_price = round(entry_price + min_tp_dist, digits)
                            elif action == 'SELL' and (entry_price - tp_price) < min_tp_dist:
                                tp_price = round(entry_price - min_tp_dist, digits)

                            tp_dist = abs(tp_price - entry_price)
                            rr_ratio = round(tp_dist / sl_dist, 2) if sl_dist > 0 else min_rr
                            reason = ai_decision.get('reason', '-')

                            print_signal_box(
                                symbol=symbol,
                                action=action,
                                entry_price=entry_price,
                                sl_price=sl_price,
                                tp_price=tp_price,
                                sl_dist=sl_dist,
                                tp_dist=tp_dist,
                                rr_ratio=rr_ratio,
                                confidence=confidence,
                                reason=reason,
                                digits=digits,
                                base_tf=base_tf_label,
                                primary_tf=primary_tf_label,
                                primary_trend=primary_trend,
                                macro_tf=macro_tf_label,
                                macro_trend=macro_trend
                            )
                        elif action in ['PENDING_BUY', 'PENDING_SELL'] and confidence >= settings.AI_MIN_CONFIDENCE:
                            trigger_price = ai_decision.get('trigger_price', 0.0)
                            sl_price = ai_decision.get('sl_price', 0.0)
                            tp_price = ai_decision.get('tp_price', 0.0)
                            expire_seconds = ai_decision.get('expire_seconds', 60.0)
                            min_rr = getattr(settings, "MIN_RR_RATIO", 1.5)
                            sl_dist = abs(trigger_price - sl_price)
                            min_tp_dist = sl_dist * min_rr
                            if action == 'PENDING_BUY' and (tp_price - trigger_price) < min_tp_dist:
                                tp_price = round(trigger_price + min_tp_dist, digits)
                            elif action == 'PENDING_SELL' and (trigger_price - tp_price) < min_tp_dist:
                                tp_price = round(trigger_price - min_tp_dist, digits)
                            tp_dist = abs(tp_price - trigger_price)
                            rr_ratio = round(tp_dist / sl_dist, 2) if sl_dist > 0 else min_rr
                            reason = ai_decision.get('reason', '-')

                            set_trigger_plan(symbol, action, trigger_price, sl_price, tp_price, confidence, expire_seconds)
                            logger.info(
                                f"[SIGNAL ONLY] Trigger Plan Disimpan: {action} @ {trigger_price:.{digits}f} | "
                                f"SL: {sl_price:.{digits}f} | TP: {tp_price:.{digits}f} | Expire: {expire_seconds:.0f}s (Python Sniper memantau tick...)"
                            )
                            print_signal_box(
                                symbol=symbol,
                                action=action,
                                entry_price=trigger_price,
                                sl_price=sl_price,
                                tp_price=tp_price,
                                sl_dist=sl_dist,
                                tp_dist=tp_dist,
                                rr_ratio=rr_ratio,
                                confidence=confidence,
                                reason=f"[TRIGGER BREAKOUT @ {trigger_price:.{digits}f}] {reason}",
                                digits=digits,
                                base_tf=base_tf_label,
                                primary_tf=primary_tf_label,
                                primary_trend=primary_trend,
                                macro_tf=macro_tf_label,
                                macro_trend=macro_trend
                            )
                        elif action in ['MODIFY', 'CLOSE']:
                            logger.info(f"[SIGNAL ONLY] AI Rekomendasi Taktis: {action} (Conf: {confidence:.2f}) | Alasan: {ai_decision.get('reason', '-')}")

                    else:
                        # --- MODE 1: AUTO TRADING (FULL EXECUTION) - AI COMMANDER PURE ---
                        current_positions = get_active_positions(symbol)
                        has_active_pos = len(current_positions) > 0

                        if has_active_pos:
                            # --- RE-EVALUASI POSISI AKTIF OLEH AI ---
                            for pos in current_positions:
                                pos_type = pos.type # 0 = BUY, 1 = SELL
                                ticket = pos.ticket

                                # A. Sinyal MODIFY taktis oleh AI (menaikkan/menurunkan SL atau TP)
                                if action == 'MODIFY' and confidence >= settings.AI_MIN_CONFIDENCE:
                                    new_sl = ai_decision.get('new_sl') or ai_decision.get('entry_sl') or ai_decision.get('sl_price', 0.0)
                                    new_tp = ai_decision.get('new_tp') or ai_decision.get('entry_tp') or ai_decision.get('tp_price', 0.0)
                                    apply_ai_position_modification(symbol, ticket, new_sl, new_tp, action_type="MODIFY")

                                # B. Sinyal CLOSE atau Reversal Arah Ekstrem
                                elif (action == 'CLOSE' or (action == 'SELL' and pos_type == mt5.ORDER_TYPE_BUY) or (action == 'BUY' and pos_type == mt5.ORDER_TYPE_SELL)) and confidence >= settings.AI_MIN_CONFIDENCE:
                                    logger.info(f"[REVERSAL/CLOSE] AI merekomendasikan {action}! Menutup posisi tiket {ticket}...")
                                    if close_position(ticket):
                                        current_positions = get_active_positions(symbol)
                                        has_active_pos = len(current_positions) > 0

                                # C. Sinyal HOLD (Pertahankan posisi)
                                elif action == 'HOLD':
                                    logger.info(f"[AI POSITION HOLD] Posisi tiket {ticket} dipertahankan sesuai target awal. Alasan: {ai_decision.get('reason', '-')}")

                        else:
                            # --- MODE PENCARIAN PELUANG MASUK BARU (BUY / SELL / PENDING TRIGGER) ---
                            if action in ['BUY', 'SELL'] and confidence >= settings.AI_MIN_CONFIDENCE and len(current_positions) < settings.MAX_OPEN_POSITIONS:
                                sl_price = ai_decision.get('sl_price', 0.0)
                                tp_price = ai_decision.get('tp_price', 0.0)

                                entry_price = tick.ask if action == 'BUY' else tick.bid
                                sl_dist_price = abs(entry_price - sl_price)

                                lot = calculate_lot_size(symbol, sl_dist_price=sl_dist_price)

                                if lot <= 0 or sl_dist_price <= 0 or sl_price <= 0:
                                    logger.error("[ERROR] SL dari AI tidak valid atau memicu lot <= 0. Batal OP.")
                                else:
                                    order_type = mt5.ORDER_TYPE_BUY if action == 'BUY' else mt5.ORDER_TYPE_SELL
                                    comment = f"AI_{action}_{confidence:.2f}"
                                    open_position(symbol, order_type, lot, sl_price=sl_price, tp_price=tp_price, comment=comment)

                            elif action in ['PENDING_BUY', 'PENDING_SELL'] and confidence >= settings.AI_MIN_CONFIDENCE and len(current_positions) < settings.MAX_OPEN_POSITIONS:
                                trigger_price = ai_decision.get('trigger_price', 0.0)
                                sl_price = ai_decision.get('sl_price', 0.0)
                                tp_price = ai_decision.get('tp_price', 0.0)
                                expire_seconds = ai_decision.get('expire_seconds', 60.0)

                                set_trigger_plan(symbol, action, trigger_price, sl_price, tp_price, confidence, expire_seconds)
                                logger.info(
                                    f"[AI COMMANDER] Trigger Plan Disimpan: {action} @ {trigger_price:.{digits}f} | "
                                    f"SL: {sl_price:.{digits}f} | TP: {tp_price:.{digits}f} (Python Sniper aktif memantau tick...)"
                                )
            
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