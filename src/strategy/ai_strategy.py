import json
import re
import requests
import pandas as pd
from typing import Optional, Dict, Any
import MetaTrader5 as mt5
from utils.logger import logger
from config import settings
from src.strategy.base import calculate_indicators

try:
    from src.execution.order_manager import get_position_state
except ImportError:
    def get_position_state(ticket: int) -> dict:
        return {}

# Reusable HTTP Session untuk mencegah socket TIME_WAIT leak pada polling cepat M1
http_session = requests.Session()

def safe_float(val: Any, default: float = 0.0) -> float:
    """Konversi nilai ke float secara aman, menangani None, empty string, atau string non-numerik."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).strip()
    if val_str == "" or val_str.lower() in ("null", "none", "nan"):
        return default
    val_str = re.sub(r"[^\d.-]", "", val_str)
    try:
        return float(val_str)
    except (ValueError, TypeError):
        return default

def format_candles_summary(candles: list) -> str:
    """Helper untuk merangkum list candle menjadi baris teks terformat."""
    if not candles:
        return "  (Data candle tidak tersedia)"
    lines = []
    for c in candles:
        lines.append(
            f"  [{c.get('time', '-')}] O: {c.get('open', 0):.2f}, H: {c.get('high', 0):.2f}, "
            f"L: {c.get('low', 0):.2f}, C: {c.get('close', 0):.2f}, Vol: {c.get('volume', 0)}"
        )
    return "\n".join(lines)

def build_prompt_context(market_input: Any, tick: Any, active_positions: list) -> str:
    """
    Menyusun ringkasan kondisi pasar Multi-Timeframe (M15, M5, M1) untuk diumpankan ke model AI.
    Mendukung input mtf_data (dict) maupun fallback DataFrame (single timeframe).
    Menyertakan blok evaluasi posisi aktif secara mendalam (in-position context) dan analisis sumbu lilin M1.
    """
    spread = tick.ask - tick.bid

    # Jika input berupa dictionary Multi-Timeframe
    if isinstance(market_input, dict):
        metadata = market_input.get("metadata", {})
        digits = int(metadata.get("digits", 2))
        point = float(metadata.get("point", 0.01))
        symbol_name = str(metadata.get("name", settings.SYMBOL))
        stops_level = int(metadata.get("stops_level", 30))
        stops_level_dist = float(metadata.get("stops_level_dist", stops_level * point))

        hierarchy = market_input.get("hierarchy", {})
        base_tf = str(hierarchy.get("base", getattr(settings, "TIMEFRAME_STR", "M1"))).upper()
        primary_tf = str(hierarchy.get("primary", "M5")).upper()
        macro_tf = str(hierarchy.get("macro", "M15")).upper()

        base_data = market_input.get("base") or market_input.get(base_tf.lower()) or {}
        primary_data = market_input.get("primary") or market_input.get(primary_tf.lower()) or {}
        macro_data = market_input.get("macro") or market_input.get(macro_tf.lower()) or {}

        base_str = format_candles_summary(base_data.get("recent_candles", []))
        primary_str = format_candles_summary(primary_data.get("recent_candles", []))
        macro_str = format_candles_summary(macro_data.get("recent_candles", []))

        base_atr = float(base_data.get('atr', 0.0))
        base_close = float(base_data.get('latest_close', tick.bid))
        is_gold = "XAU" in symbol_name.upper() or "GOLD" in symbol_name.upper()

        if is_gold:
            min_sl_dist = 1.80
            max_sl_dist = 2.50
        elif digits >= 3:
            min_sl_dist = round(40.0 * point, digits)
            max_sl_dist = round(70.0 * point, digits)
        else:
            min_sl_dist = round(1.5 * base_atr, digits)
            max_sl_dist = round(2.0 * base_atr, digits)

        min_tp_dist = round(min_sl_dist * settings.MIN_RR_RATIO, digits)
        target_tp_dist = round(min_sl_dist * settings.TARGET_RR_RATIO, digits)

        # Hitung sumbu lilin Base Timeframe akhir (Wick Analysis)
        recent_base = base_data.get("recent_candles", [])
        if recent_base:
            last_c = recent_base[-1]
            c_open = float(last_c.get("open", 0.0))
            c_high = float(last_c.get("high", 0.0))
            c_low = float(last_c.get("low", 0.0))
            c_close = float(last_c.get("close", 0.0))
            c_range = c_high - c_low
            b_top = max(c_open, c_close)
            b_bot = min(c_open, c_close)
            u_wick = max(0.0, c_high - b_top)
            l_wick = max(0.0, b_bot - c_low)
            upper_pct = round((u_wick / c_range) * 100, 1) if c_range > 0 else 0.0
            lower_pct = round((l_wick / c_range) * 100, 1) if c_range > 0 else 0.0
        else:
            upper_pct = 0.0
            lower_pct = 0.0

        # Rangkum blok status posisi aktif secara detail jika sedang in-position
        if active_positions:
            pos = active_positions[0]
            ticket = pos.ticket
            pos_type_str = "BUY" if pos.type == 0 else "SELL"
            price_open = pos.price_open
            current_sl = pos.sl
            current_tp = pos.tp
            volume = pos.volume

            sl_dist_points = round(abs(price_open - current_sl) / point, 1) if current_sl > 0 else 0.0
            tp_dist_points = round(abs(current_tp - price_open) / point, 1) if current_tp > 0 else 0.0

            if pos.type == 0:
                pips_or_points = round((tick.bid - price_open) / point, 1)
            else:
                pips_or_points = round((price_open - tick.ask) / point, 1)

            account = mt5.account_info() if hasattr(mt5, "account_info") else None
            is_idr = account and getattr(account, "currency", "").upper() == "IDR"
            if is_idr:
                profit_currency = f"Rp{pos.profit:,.0f}"
            else:
                profit_currency = f"${pos.profit:.2f} (Rp{pos.profit * settings.KURS_USD_IDR:,.0f})"

            p_state = get_position_state(ticket)
            current_mkt = tick.bid if pos.type == 0 else tick.ask
            extreme_price = p_state.get("extreme_price", current_mkt)
            if pos.type == 0:
                peak_profit_points = round(max(0.0, extreme_price - price_open) / point, 1)
            else:
                peak_profit_points = round(max(0.0, price_open - extreme_price) / point, 1)

            pos_block = f"""=== [STATUS POSISI AKTIF SAAT INI] ===
- Tiket Posisi        : {ticket}
- Tipe Transaksi      : {pos_type_str}
- Volume Lot          : {volume} lot
- Harga Entry         : {price_open:.{digits}f}
- Stop Loss Saat Ini  : {current_sl:.{digits}f} (Jarak: {sl_dist_points} poin)
- Take Profit Saat Ini: {current_tp:.{digits}f} (Jarak: {tp_dist_points} poin)
- Harga Pasar Saat Ini: Bid={tick.bid:.{digits}f} | Ask={tick.ask:.{digits}f}
- Floating Profit/Loss: {profit_currency} (Setara {pips_or_points} poin)
- Rekor Jarak Puncak  : {peak_profit_points} poin
- Sumbu Lilin {base_tf} Akhir: Upper Wick={upper_pct}%, Lower Wick={lower_pct}% dari total rentang lilin."""
        else:
            pos_block = """=== [STATUS POSISI AKTIF SAAT INI] ===
- Status: FLAT (Tidak ada posisi terbuka)"""

        context = f"""[STATUS AKUN & INSTRUMEN PASAR]
Instrumen Perdagangan : {symbol_name} (Presisi: {digits} desimal, 1 Point: {point})
Harga Pasar Terkini   : Bid = {tick.bid:.{digits}f} | Ask = {tick.ask:.{digits}f} | Spread = {spread:.{digits}f} ({spread/point:.1f} poin)
Lilin {base_tf} Terakhir     : Close = {base_close:.{digits}f} | ATR {base_tf} = {base_atr:.{digits}f}
Batasan Matematis     : SL Wajib = {min_sl_dist:.{digits}f} s/d {max_sl_dist:.{digits}f} (Skala {base_tf} ATR, DILARANG swing {primary_tf} > {max_sl_dist:.{digits}f}) | TP Wajib = >= {min_tp_dist:.{digits}f} s/d {target_tp_dist:.{digits}f} (Minimal R:R 1:{settings.MIN_RR_RATIO:.1f} s/d 1:{settings.TARGET_RR_RATIO:.1f}, DILARANG KERAS R:R < 1:1.5!) | Min Stop Broker = {stops_level_dist:.{digits}f} ({stops_level} poin)
INSTRUKSI HARGA       : Seluruh angka entry_sl, entry_tp, new_sl, new_tp WAJIB ditulis dalam format harga absolut {digits} desimal!

{pos_block}

=== [{macro_tf} - MACRO TIMEFRAME (REFERENSI STRUKTURAL MAYOR - TANPA HAK VETO)] ===
- Peran              : Referensi S/R Mayor & Konteks Besar (TIDAK MEMILIKI HAK VETO membatalkan Primary TF)
- Tren & Bias        : {macro_data.get('trend', 'NEUTRAL')}
- EMA 9 vs EMA 21    : EMA9={macro_data.get('ema9', 0)}, EMA21={macro_data.get('ema21', 0)}
- RSI (14) & ATR (14): RSI={macro_data.get('rsi', 0)}, ATR={macro_data.get('atr', 0)}
- 5 Candle Terakhir {macro_tf} (OHLC):
{macro_str}

=== [{primary_tf} - PRIMARY TIMEFRAME (ACUAN UTAMA & PENGAMBIL KEPUTUSAN MUTLAK)] ===
- Peran              : ACUAN UTAMA & PENGAMBIL KEPUTUSAN MUTLAK (Bullish -> BUY/PENDING_BUY, Bearish -> SELL/PENDING_SELL)
- Tren & Struktur    : {primary_data.get('trend', 'NEUTRAL')}
- EMA 9 vs EMA 21    : EMA9={primary_data.get('ema9', 0)}, EMA21={primary_data.get('ema21', 0)}
- RSI (14) & ATR (14): RSI={primary_data.get('rsi', 0)}, ATR={primary_data.get('atr', 0)}
- 5 Candle Terakhir {primary_tf} (OHLC):
{primary_str}

=== [{base_tf} - BASE TIMEFRAME (PELATUK EKSEKUSI MIKRO, WICK FILTER & KALKULASI SL/TP)] ===
- Peran              : Pemicu Masuk (Trigger), Rejection Wick Filter DAN Penentu Mutlak Jarak Harga (SL & TP)
- Tren Mikro         : {base_data.get('trend', 'NEUTRAL')}
- EMA 9 vs EMA 21    : EMA9={base_data.get('ema9', 0)}, EMA21={base_data.get('ema21', 0)}
- RSI (14) & ATR (14): RSI={base_data.get('rsi', 0)}, ATR={base_data.get('atr', 0)}
- 5 Candle Terakhir {base_tf} (OHLC):
{base_str}"""
        return context
        return context

    # Fallback jika input berupa single DataFrame
    df = market_input
    if 'ema_9' not in df.columns:
        df = calculate_indicators(df)
        
    recent_candles = df.iloc[-6:-1].copy()
    latest = df.iloc[-1]
    
    candles_summary = []
    for idx, row in recent_candles.iterrows():
        candles_summary.append(
            f"Waktu: {idx}, O: {row['open']:.2f}, H: {row['high']:.2f}, L: {row['low']:.2f}, C: {row['close']:.2f}"
        )
    candles_str = "\n".join(candles_summary)
    
    pos_desc = "FLAT (Tidak ada posisi terbuka)"
    if active_positions:
        p = active_positions[0]
        pos_desc = f"{'BUY' if p.type == 0 else 'SELL'} {p.volume} lot @ {p.price_open:.2f}"
    context = f"""Status Posisi Saat Ini: {pos_desc}
Harga Pasar Saat Ini: Bid = {tick.bid:.2f}, Ask = {tick.ask:.2f}, Spread = {spread:.2f}

Indikator Teknikal Terkini (Candle Terakhir):
- EMA 9: {latest['ema_9']:.2f}
- EMA 21: {latest['ema_21']:.2f}
- RSI 14: {latest['rsi_14']:.2f}
- ATR 14: {latest['atr_14']:.2f}

Data 5 Candle Terakhir (OHLC):
{candles_str}"""
    return context

def parse_stream_response(raw_text: str) -> str:
    """
    Menggabungkan seluruh potongan teks dari Server-Sent Events (SSE / mode Streaming) 
    kompatibel dengan format OpenAI yang dikembalikan oleh server LLM lokal.
    """
    combined_content = ""
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
            
        if line.startswith("data: "):
            json_str = line[6:].strip() # Hapus prefix "data: "
            if json_str == "[DONE]":
                break
            try:
                chunk = json.loads(json_str)
                choices = chunk.get("choices", [])
                if choices:
                    # Pada format streaming, isi teks ada di delta.content bukan message.content
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        combined_content += content
            except json.JSONDecodeError:
                continue
                
    return combined_content

def clean_and_parse_json(raw_text: str) -> Optional[Dict[str, Any]]:
    """
    Membersihkan teks respons dari LLM dan mengekstrak objek JSON yang valid.
    Tahan terhadap tag markdown, teks pengantar/penutup, dan trailing commas.
    """
    if not raw_text:
        return None

    cleaned = raw_text.strip()

    # 1. Ekstrak blok di dalam ```json ... ``` jika ada di mana pun dalam string
    codeblock_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if codeblock_match:
        cleaned = codeblock_match.group(1).strip()
    else:
        # Hapus sisa backticks jika ada di awalan/akhiran
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    # 2. Coba parse langsung setelah strip markdown
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3. Ekstrak dengan regex mencari blok JSON di antara kurung kurawal pertama { dan terakhir }
    match = re.search(r"(\{.*\})", cleaned, flags=re.DOTALL)
    if match:
        json_candidate = match.group(1).strip()
        # Bersihkan trailing commas yang sering dihasilkan LLM: misal: '..., "tp": 2750, }'
        json_candidate = re.sub(r",\s*([\]}])", r"\1", json_candidate)
        try:
            return json.loads(json_candidate)
        except json.JSONDecodeError:
            pass

        # Fallback: Tangani output kamus Python berkutip tunggal
        try:
            import ast
            py_dict = ast.literal_eval(json_candidate)
            if isinstance(py_dict, dict):
                return py_dict
        except Exception:
            pass

    return None

def get_ai_decision(market_input: Any, tick: Any, active_positions: list) -> Dict[str, Any]:
    """
    Mengirimkan konteks Multi-Timeframe ke REST API LLM dan mengurai respons berformat JSON.
    Fallback ke 'HOLD' jika terjadi error atau timeout.
    """
    hierarchy = market_input.get("hierarchy", {}) if isinstance(market_input, dict) else {}
    base_tf = str(hierarchy.get("base", getattr(settings, "TIMEFRAME_STR", "M1"))).upper()
    primary_tf = str(hierarchy.get("primary", "M5")).upper()
    macro_tf = str(hierarchy.get("macro", "M15")).upper()

    fallback_response = {
        "action": "HOLD",
        "signal": "HOLD",
        "confidence": 0.0,
        "primary_bias": "NEUTRAL",
        "macro_bias": "NEUTRAL",
        "m15_bias": "NEUTRAL",
        "m5_bias": "NEUTRAL",
        "hierarchy": {
            "base": base_tf,
            "primary": primary_tf,
            "macro": macro_tf
        },
        "entry_price": 0.0,
        "trigger_price": 0.0,
        "sl_price": 0.0,
        "tp_price": 0.0,
        "entry_sl": 0.0,
        "entry_tp": 0.0,
        "new_sl": 0.0,
        "new_tp": 0.0,
        "expire_seconds": 60.0,
        "sl_distance_usd": 0.0,
        "tp_distance_usd": 0.0,
        "risk_reward_ratio": 0.0,
        "reason": "Fallback default karena error atau sinyal lemah"
    }

    context_prompt = build_prompt_context(market_input, tick, active_positions)
    
    system_prompt = f"""Anda adalah Institutional Quantitative Scalper Multi-Asset (Forex, Komoditas, Indeks) yang bertindak sebagai "AI Commander".
Analisa data pasar dari 3 Timeframe ({macro_tf}, {primary_tf}, {base_tf}) dengan hierarki pembobotan keputusan yang tegas:

[HIERARKI KEPUTUSAN & PEMBOBOTAN TIMEFRAME]
1. PRIMARY TIMEFRAME ({primary_tf}) - ACUAN UTAMA & PENGAMBIL KEPUTUSAN MUTLAK:
   - Merupakan ACUAN UTAMA & PENGAMBIL KEPUTUSAN MUTLAK bagi sistem.
   - JIKA {primary_tf} BULLISH (Harga > EMA 21 & RSI >= 50) -> Bias WAJIB BUY / PENDING_BUY.
   - JIKA {primary_tf} BEARISH (Harga < EMA 21 & RSI <= 50) -> Bias WAJIB SELL / PENDING_SELL.

2. MACRO TIMEFRAME ({macro_tf}) - REFERENSI STRUKTURAL SAJA (TIDAK MEMILIKI HAK VETO):
   - Hanya bertindak sebagai REFERENSI STRUKTURAL (melihat level support/resistance mayor terdekat dan konteks pergerakan besar).
   - {macro_tf} TIDAK MEMILIKI HAK VETO untuk membatalkan keputusan yang sudah terkonfirmasi kuat oleh Primary TF ({primary_tf}).

3. BASE TIMEFRAME ({base_tf}) - PELATUK EKSEKUSI MIKRO, WICK FILTER & KALKULASI HARGA:
   - Digunakan murni sebagai konfirmasi mikro titik masuk (trigger entry), perhitungan Stop Loss ATR {base_tf}, dan filter penolakan sumbu (rejection wick).
   - DILARANG KERAS menggunakan swing structure {macro_tf} atau {primary_tf} untuk menentukan SL atau TP!

[RE-EVALUASI POSISI AKTIF (JIKA SEDANG IN-POSITION)]
Jika pada blok [STATUS POSISI AKTIF SAAT INI] terdapat posisi terbuka, evaluasi posisi dengan 3 opsi aksi taktis:
1. HOLD: Pertahankan posisi dengan level SL/TP saat ini karena tren {primary_tf} masih valid mendukung target awal.
2. MODIFY: Jika harga bergerak sesuai arah namun {base_tf} membentuk swing mikro baru atau indikasi pelemahan momentum (misal rejection wick), naikkan/turunkan SL untuk mengunci profit.
   - Wajib menyertakan field: "new_sl" dan "new_tp". new_sl DILARANG KERAS memperlebar risiko (BUY: new_sl >= SL lama; SELL: new_sl <= SL lama).
3. CLOSE: Eksekusi penutupan posisi darurat seketika jika struktur {primary_tf} atau {base_tf} menunjukkan pola pembalikan arah ekstrem (reversal) yang mengancam akun.

[PENCARIAN PELUANG MASUK BARU - AI COMMANDER & TRIGGER PLAN (JIKA FLAT / TIDAK ADA POSISI)]
Jika saat ini status adalah FLAT (tidak ada posisi terbuka), putuskan salah satu aksi berikut:
1. BUY / SELL (Eksekusi Instan di Detik ke-0):
   - Gunakan HANYA JIKA {primary_tf} terkonfirmasi searah dan momentum lilin {base_tf} sudah meledak breakout saat lilin dibuka.
2. PENDING_BUY (Rencana Trigger Breakout Ke Atas):
   - Jika {primary_tf} Bullish kuat, pasang jebakan masuk jika harga {base_tf} menembus level tertentu (High lilin sebelumnya). Wajib sertakan: "trigger_price" (> Ask), "sl_price", "tp_price", "expire_seconds" (default 60).
3. PENDING_SELL (Rencana Trigger Breakdown Ke Bawah):
   - Jika {primary_tf} Bearish kuat, pasang jebakan masuk jika harga {base_tf} menembus level tertentu (Low lilin sebelumnya). Wajib sertakan: "trigger_price" (< Bid), "sl_price", "tp_price", "expire_seconds" (default 60).
4. HOLD:
   - Jika {primary_tf} dalam kondisi konsolidasi ketat (Neutral) dan {base_tf} tidak memiliki arah momentum yang jelas.

[ATURAN KUANTITATIF SL & TP BERBASIS ATR {base_tf} (TIDAK BISA DITAWAR)]
1. Kalkulasi SL Berdasarkan Volatilitas {base_tf} (Bukan {primary_tf} / {macro_tf}):
   - Untuk XAUUSD: Jarak SL WAJIB berkisar antara $1.80 hingga $2.50 dari harga Entry/Trigger (skala ATR {base_tf}). DILARANG KERAS mengambil level swing {primary_tf} yang berjarak >$3.00!
   - Untuk Forex Majors (EURUSD/GBPUSD): Jarak SL berkisar 4 – 7 pips (0.0004 – 0.0007).
2. Kewajiban Rasio Risk-to-Reward (R:R Minimal 1:1.5 hingga 1:2.0):
   - Jarak TP DILARANG KERAS lebih kecil daripada 1.5x jarak SL! (DILARANG R:R < 1:1.5).
   - Rumus Jarak TP: Minimal 1.5x hingga 2.0x dari jarak SL (tp_distance >= sl_distance * 1.5).
   - Target TP harus mempertimbangkan level support/resistance atau EMA {primary_tf}/{macro_tf} terdekat sebagai pembatas logis.
3. Validasi Stop Level Broker:
   - Jarak SL dan TP terhadap harga pasar saat ini (Ask/Bid) wajib menghormati batas minimal broker (Min Stop Broker).
4. Batas Logis Harga & Format Angka:
   - Jika "BUY" / "PENDING_BUY" : sl_price < entry_price/trigger_price < tp_price
   - Jika "SELL" / "PENDING_SELL": sl_price > entry_price/trigger_price > tp_price
   - Seluruh nilai harga entry_sl, entry_tp, new_sl, new_tp, trigger_price wajib berupa angka harga absolut yang presisi sesuai jumlah digit desimal instrumen target.

INSTRUKSI FORMAT OUTPUT (STRICT JSON ONLY):
HANYA kembalikan teks raw JSON valid tanpa tambahan markdown ```json ... ``` atau teks pengantar apa pun.
Format JSON Output Wajib:
{{
  "action": "BUY" | "SELL" | "HOLD" | "MODIFY" | "CLOSE" | "PENDING_BUY" | "PENDING_SELL",
  "confidence": <float 0.0 - 1.0>,
  "primary_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "macro_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "entry_price": <float>,
  "trigger_price": <float>,
  "entry_sl": <float>,
  "entry_tp": <float>,
  "sl_price": <float>,
  "tp_price": <float>,
  "new_sl": <float>,
  "new_tp": <float>,
  "expire_seconds": <float default 60.0>,
  "sl_distance_usd": <float>,
  "tp_distance_usd": <float>,
  "risk_reward_ratio": <float>,
  "reason": "<Alasan taktis berbasis Primary {primary_tf} dan konfirmasi {base_tf}>"
}}"""

    api_key = settings.AI_API_KEY or "not-needed"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Payload
    payload = {
        "model": settings.AI_MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context_prompt}
        ],
        "temperature": 0.2,
        "stream": False # Matikan mode streaming secara eksplisit
    }

    base_url = settings.AI_API_BASE_URL.rstrip('/')
    if base_url.endswith("/chat/completions"):
        url = base_url
    else:
        url = f"{base_url}/chat/completions"

    logger.info(f"[INFO] Meminta analisis MTF dari AI ke endpoint: {url}")
    
    try:
        response = http_session.post(url, headers=headers, json=payload, timeout=15)
        
        if response.status_code != 200:
            logger.error(f"[ERROR] HTTP Error dari server LLM: Status Code {response.status_code}")
            logger.error(f"[DEBUG] Isi Respons Mentah: {repr(response.text)}")
            return fallback_response

        # Coba parse secara standar untuk mode non-streaming
        ai_text = ""
        try:
            data = response.json()
            choices = data.get('choices')
            if choices and isinstance(choices, list) and len(choices) > 0:
                ai_text = choices[0].get('message', {}).get('content', '').strip()
            else:
                logger.error(f"[ERROR] 'choices' kosong pada respons server LLM: {data}")
                return fallback_response
                
        except json.JSONDecodeError as jde:
            # Jika gagal parse dan respons adalah format data stream (SSE)
            if "data: " in response.text:
                logger.info("[INFO] Merakit respons dari format streaming (SSE)...")
                ai_text = parse_stream_response(response.text)
                
                if not ai_text.strip():
                    logger.error("[ERROR] Gagal merakit teks dari respons streaming LLM.")
                    logger.error(f"[DEBUG] Isi Respons Mentah: {repr(response.text)}")
                    return fallback_response
            else:
                logger.error(f"[ERROR] Server tidak mengembalikan format JSON valid: {jde}")
                logger.error(f"[DEBUG] Isi Respons Server: {repr(response.text)}")
                return fallback_response
        
        # Ekstrak string JSON hasil perakitan (baik via streaming / non-streaming)
        decision = clean_and_parse_json(ai_text)
        
        if not decision:
            logger.error(f"[ERROR] Gagal membersihkan/parsing JSON keputusan dari AI. Raw Asli AI: {repr(ai_text)}")
            return fallback_response
        
        # Ekstraksi action/signal
        action = str(decision.get('signal') or decision.get('action') or 'HOLD').upper()
        
        # Ekstraksi aman (safe float casting) menghindari error saat AI mengirim 'null' atau empty string
        confidence = safe_float(decision.get('confidence'), 0.0)
        
        reason = str(decision.get('reason', 'Tidak ada alasan'))
        primary_bias = str(decision.get('primary_bias') or decision.get('m5_bias') or 'NEUTRAL').upper()
        macro_bias = str(decision.get('macro_bias') or decision.get('m15_bias') or 'NEUTRAL').upper()

        metadata = market_input.get("metadata", {}) if isinstance(market_input, dict) else {}
        digits = int(metadata.get("digits", 2))
        symbol_name = str(metadata.get("name", settings.SYMBOL))

        # Entry price (ambil dari AI atau gunakan harga pasar terkini)
        market_entry = tick.ask if action == 'BUY' else tick.bid
        entry_price = safe_float(decision.get('entry_price'), market_entry)
        if entry_price <= 0:
            entry_price = market_entry
        entry_price = round(entry_price, digits)
        
        sl_val = decision.get('entry_sl') if decision.get('entry_sl') is not None else decision.get('sl_price')
        sl_price = safe_float(sl_val, 0.0)
        
        tp_val = decision.get('entry_tp') if decision.get('entry_tp') is not None else decision.get('tp_price')
        tp_price = safe_float(tp_val, 0.0)

        # Ekstraksi new_sl & new_tp jika action == 'MODIFY'
        new_sl_val = decision.get('new_sl') if decision.get('new_sl') is not None else (decision.get('entry_sl') or decision.get('sl_price'))
        new_sl = safe_float(new_sl_val, sl_price)

        new_tp_val = decision.get('new_tp') if decision.get('new_tp') is not None else (decision.get('entry_tp') or decision.get('tp_price'))
        new_tp = safe_float(new_tp_val, tp_price)

        # Ekstraksi trigger_price & expire_seconds jika action == 'PENDING_BUY' atau 'PENDING_SELL'
        trigger_val = decision.get('trigger_price')
        trigger_price = safe_float(trigger_val, 0.0)
        expire_seconds = safe_float(decision.get('expire_seconds'), 60.0)
        if expire_seconds <= 0.0:
            expire_seconds = 60.0

        is_gold = "XAU" in symbol_name.upper() or "GOLD" in symbol_name.upper()
        point = float(metadata.get("point", 0.01))
        stops_level_dist = float(metadata.get("stops_level_dist", 30 * point))

        # Kunci Pengaman Matematis di Sisi Kode Python (Safety Guardrail)
        min_rr = getattr(settings, "MIN_RR_RATIO", 1.5)
        target_rr = getattr(settings, "TARGET_RR_RATIO", 2.0)

        if action in ['BUY', 'SELL'] and sl_price > 0:
            # Pastikan arah SL dasar benar terhadap Entry
            if action == 'BUY' and sl_price >= entry_price:
                sl_price = round(entry_price - (2.0 if is_gold else 0.0005), digits)
            elif action == 'SELL' and sl_price <= entry_price:
                sl_price = round(entry_price + (2.0 if is_gold else 0.0005), digits)

            # 1. Hitung jarak mutlak SL dan TP
            sl_distance = abs(entry_price - sl_price)
            tp_distance = abs(tp_price - entry_price) if tp_price > 0 else 0.0

            # 2. Validasi batas atas SL M1 (Cap SL jika terlalu lebar)
            sl_cap = getattr(settings, "SL_CAP_PRICE", 2.50 if is_gold else 0.0025)
            if sl_distance > sl_cap:
                sl_before = sl_price
                sl_dist_before = sl_distance
                if action == 'BUY':
                    sl_price = round(entry_price - sl_cap, digits)
                elif action == 'SELL':
                    sl_price = round(entry_price + sl_cap, digits)
                sl_distance = abs(entry_price - sl_price)
                logger.info(f"[SL CAP GUARD] SL {sl_before:.{digits}f} terlalu lebar ({sl_dist_before:.{digits}f} > {sl_cap:.{digits}f}). Dikoreksi maksimal ke {sl_cap:.{digits}f} -> SL baru: {sl_price:.{digits}f}")

            # 3. Koreksi Otomatis Rasio R:R (Enforce Minimum R:R = settings.MIN_RR_RATIO, default 1.5)
            min_tp_dist = sl_distance * min_rr
            if tp_distance < min_tp_dist or (action == 'BUY' and tp_price <= entry_price) or (action == 'SELL' and tp_price >= entry_price):
                if action == 'BUY':
                    tp_price = round(entry_price + min_tp_dist, digits)
                elif action == 'SELL':
                    tp_price = round(entry_price - min_tp_dist, digits)
                tp_distance = abs(tp_price - entry_price)
                logger.info(f"[R:R GUARD] TP disesuaikan otomatis ke {tp_price:.{digits}f} agar memenuhi syarat minimal R:R 1:{min_rr:.1f}.")

            sl_distance_usd = sl_distance
            tp_distance_usd = tp_distance
            risk_reward_ratio = round(tp_distance / sl_distance, 2) if sl_distance > 0 else 0.0

            if risk_reward_ratio < min_rr:
                logger.info(f"[R:R REJECTED] Sinyal {action} ditolak karena R:R (1:{risk_reward_ratio:.2f}) < Minimal (1:{min_rr:.1f}). Fallback ke HOLD.")
                action = 'HOLD'
                decision['action'] = 'HOLD'
                decision['signal'] = 'HOLD'
                decision['reason'] = f"Setup ditolak: Rasio R:R di bawah batas minimum 1:{min_rr:.1f}"

        elif action in ['PENDING_BUY', 'PENDING_SELL']:
            # Guardrail Matematis Trigger Plan Sniper (Filter Buffer Anti-Noise minimal $0.25 / 25 poin)
            min_trigger_buffer = max(0.25 if is_gold else 25 * point, stops_level_dist)

            if action == 'PENDING_BUY':
                if trigger_price <= 0.0 or (trigger_price - tick.ask) < min_trigger_buffer:
                    trigger_price = round(tick.ask + min_trigger_buffer, digits)
                if sl_price >= trigger_price or sl_price <= 0.0:
                    sl_price = round(trigger_price - (2.0 if is_gold else 40 * point), digits)
                sl_distance = abs(trigger_price - sl_price)
                sl_cap = getattr(settings, "SL_CAP_PRICE", 2.50 if is_gold else 0.0025)
                if sl_distance > sl_cap:
                    sl_price = round(trigger_price - sl_cap, digits)
                    sl_distance = sl_cap
                min_tp_dist = sl_distance * min_rr
                if tp_price <= trigger_price or abs(tp_price - trigger_price) < min_tp_dist:
                    tp_price = round(trigger_price + min_tp_dist, digits)
                tp_distance = abs(tp_price - trigger_price)
                risk_reward_ratio = round(tp_distance / sl_distance, 2) if sl_distance > 0 else 0.0
            else: # PENDING_SELL
                if trigger_price <= 0.0 or (tick.bid - trigger_price) < min_trigger_buffer:
                    trigger_price = round(tick.bid - min_trigger_buffer, digits)
                if sl_price <= trigger_price or sl_price <= 0.0:
                    sl_price = round(trigger_price + (2.0 if is_gold else 40 * point), digits)
                sl_distance = abs(trigger_price - sl_price)
                sl_cap = getattr(settings, "SL_CAP_PRICE", 2.50 if is_gold else 0.0025)
                if sl_distance > sl_cap:
                    sl_price = round(trigger_price + sl_cap, digits)
                    sl_distance = sl_cap
                min_tp_dist = sl_distance * min_rr
                if tp_price >= trigger_price or abs(trigger_price - tp_price) < min_tp_dist:
                    tp_price = round(trigger_price - min_tp_dist, digits)
                tp_distance = abs(tp_price - trigger_price)
                risk_reward_ratio = round(tp_distance / sl_distance, 2) if sl_distance > 0 else 0.0

            trigger_price = round(trigger_price, digits)
            sl_price = round(sl_price, digits)
            tp_price = round(tp_price, digits)
            sl_distance_usd = sl_distance
            tp_distance_usd = tp_distance

            if risk_reward_ratio < min_rr:
                logger.info(f"[R:R REJECTED] Trigger Plan {action} ditolak karena R:R (1:{risk_reward_ratio:.2f}) < Minimal (1:{min_rr:.1f}). Fallback ke HOLD.")
                action = 'HOLD'
                decision['action'] = 'HOLD'
                decision['signal'] = 'HOLD'
                decision['reason'] = f"Setup ditolak: Rasio R:R di bawah batas minimum 1:{min_rr:.1f}"
        else:
            sl_distance_usd = 0.0
            tp_distance_usd = 0.0
            risk_reward_ratio = 0.0
        
        # Simpan kembali format bersih ke dictionary (kompatibilitas multi-field)
        decision['action'] = action
        decision['signal'] = action
        decision['confidence'] = confidence
        decision['primary_bias'] = primary_bias
        decision['macro_bias'] = macro_bias
        decision['m5_bias'] = primary_bias
        decision['m15_bias'] = macro_bias
        decision['hierarchy'] = {
            "base": base_tf,
            "primary": primary_tf,
            "macro": macro_tf
        }
        decision['entry_price'] = entry_price
        decision['trigger_price'] = trigger_price
        decision['sl_price'] = sl_price
        decision['tp_price'] = tp_price
        decision['entry_sl'] = sl_price
        decision['entry_tp'] = tp_price
        decision['new_sl'] = new_sl
        decision['new_tp'] = new_tp
        decision['expire_seconds'] = expire_seconds
        decision['sl_distance_usd'] = sl_distance_usd
        decision['tp_distance_usd'] = tp_distance_usd
        decision['risk_reward_ratio'] = risk_reward_ratio
        decision['reason'] = reason

        # Logging informatif sesuai instruksi
        logger.info(f"[AI EVALUATION] Sinyal: {action} (Conf: {confidence:.2f}) | Primary ({primary_tf}): {primary_bias} | Macro ({macro_tf}): {macro_bias}")
        if action == 'MODIFY':
            logger.info(f"[AI POSITION MODIFY SUGGESTION] Disarankan modifikasi taktis ke SL: {new_sl:.{digits}f} | TP: {new_tp:.{digits}f} | Alasan: {reason}")
        elif action in ['PENDING_BUY', 'PENDING_SELL']:
            logger.info(f"[AI COMMANDER] Trigger Plan: {action} @ {trigger_price:.{digits}f} | SL: {sl_price:.{digits}f} | TP: {tp_price:.{digits}f} | R:R: 1:{risk_reward_ratio:.2f} | Expire: {expire_seconds:.0f}s")
        elif action in ['BUY', 'SELL']:
            logger.info(f"[TARGET AUDIT] Entry: {entry_price:.{digits}f} | SL: {sl_price:.{digits}f} (Jarak: ${sl_distance_usd:.{digits}f}) | TP: {tp_price:.{digits}f} (Jarak: ${tp_distance_usd:.{digits}f}) | R:R: 1:{risk_reward_ratio:.2f}")
        logger.info(f"[AI REASON] {reason}")
        
        if confidence < settings.AI_MIN_CONFIDENCE and action in ['BUY', 'SELL', 'MODIFY', 'CLOSE', 'PENDING_BUY', 'PENDING_SELL']:
            logger.info(f"[AI MTF] Sinyal {action} diabaikan karena confidence ({confidence:.2f}) < Minimum ({settings.AI_MIN_CONFIDENCE})")
            decision['action'] = 'HOLD'
            decision['signal'] = 'HOLD'
            
        return decision
        
    except requests.exceptions.Timeout:
        logger.error("[ERROR] Request ke LLM Timeout (15 detik). Server lokal mungkin sedang sibuk.")
    except requests.exceptions.ConnectionError as conn_err:
        logger.error(f"[ERROR] Gagal terhubung ke server LLM lokal di {url}: {conn_err}")
    except requests.exceptions.RequestException as req_err:
        logger.error(f"[ERROR] Request HTTP ke LLM gagal: {req_err}")
    except Exception as e:
        logger.error(f"[ERROR] Error tak terduga saat memproses keputusan AI: {e}")
        
    return fallback_response