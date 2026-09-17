import json
import re
import requests
import pandas as pd
from typing import Optional, Dict, Any
from utils.logger import logger
from config import settings
from src.strategy.base import calculate_indicators

# Reusable HTTP Session untuk mencegah socket TIME_WAIT leak pada polling cepat M1
http_session = requests.Session()

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
    """
    pos_status = "FLAT (Tidak ada posisi terbuka)"
    if active_positions:
        pos = active_positions[0]
        pos_type_str = "BUY" if pos.type == 0 else "SELL"
        pos_status = f"{pos_type_str} terbuka di harga {pos.price_open}, Lot: {pos.volume}, Profit: {pos.profit}"

    spread = tick.ask - tick.bid

    # Jika input berupa dictionary Multi-Timeframe (M15, M5, M1)
    if isinstance(market_input, dict):
        m15 = market_input.get("m15", {})
        m5 = market_input.get("m5", {})
        m1 = market_input.get("m1", {})

        m15_str = format_candles_summary(m15.get("recent_candles", []))
        m5_str = format_candles_summary(m5.get("recent_candles", []))
        m1_str = format_candles_summary(m1.get("recent_candles", []))

        context = f"""[STATUS AKUN & HARGA PASAR]
Posisi Aktif Saat Ini : {pos_status}
Harga Terkini         : Bid = {tick.bid:.2f} | Ask = {tick.ask:.2f} | Spread = {spread:.2f}

=== [M15 - MAKRO TIME FRAME (BIAS UTAMA & FILTER)] ===
- Tren & Bias        : {m15.get('trend', 'NEUTRAL')}
- EMA 9 vs EMA 21    : EMA9={m15.get('ema9', 0)}, EMA21={m15.get('ema21', 0)}
- RSI (14) & ATR (14): RSI={m15.get('rsi', 0)}, ATR={m15.get('atr', 0)}
- 5 Candle Terakhir M15 (OHLC):
{m15_str}

=== [M5 - STRUKTUR TREN & MOMENTUM ANTARA] ===
- Tren & Struktur    : {m5.get('trend', 'NEUTRAL')}
- EMA 9 vs EMA 21    : EMA9={m5.get('ema9', 0)}, EMA21={m5.get('ema21', 0)}
- RSI (14) & ATR (14): RSI={m5.get('rsi', 0)}, ATR={m5.get('atr', 0)}
- 5 Candle Terakhir M5 (OHLC):
{m5_str}

=== [M1 - PELATUK EKSEKUSI MIKRO (TRIGGER ENTRY)] ===
- Tren Mikro         : {m1.get('trend', 'NEUTRAL')}
- EMA 9 vs EMA 21    : EMA9={m1.get('ema9', 0)}, EMA21={m1.get('ema21', 0)}
- RSI (14) & ATR (14): RSI={m1.get('rsi', 0)}, ATR={m1.get('atr', 0)}
- 5 Candle Terakhir M1 (OHLC):
{m1_str}"""
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
    
    context = f"""Status Posisi Saat Ini: {pos_status}
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

    return None

def get_ai_decision(market_input: Any, tick: Any, active_positions: list) -> Dict[str, Any]:
    """
    Mengirimkan konteks Multi-Timeframe ke REST API LLM dan mengurai respons berformat JSON.
    Fallback ke 'HOLD' jika terjadi error atau timeout.
    """
    fallback_response = {
        "action": "HOLD",
        "signal": "HOLD",
        "confidence": 0.0,
        "m15_bias": "NEUTRAL",
        "m5_bias": "NEUTRAL",
        "sl_price": 0.0,
        "tp_price": 0.0,
        "entry_sl": 0.0,
        "entry_tp": 0.0,
        "reason": "Fallback default karena error atau sinyal lemah"
    }

    context_prompt = build_prompt_context(market_input, tick, active_positions)
    
    system_prompt = """Anda adalah Institutional Quantitative Scalper XAUUSD.
Analisa data pasar dari 3 Timeframe (M15, M5, M1):

[M15 - MACRO DIRECTION & FILTER]
- Wajib mendikte arah utama: JIKA M15 BEARISH (Harga < EMA 21 & RSI < 50), DILARANG KERAS MEMBUKA BUY. JIKA M15 BULLISH (Harga > EMA 21 & RSI > 50), DILARANG KERAS MEMBUKA SELL.

[M5 - STRUCTURAL MOMENTUM]
- Konfirmasi kesinambungan tren. Pastikan M5 tidak berada pada area overbought (>70) atau oversold (<30) ekstrem yang rentan pembalikan arah tajam.

[M1 - EXECUTION TRIGGER]
- Cari momentum masuk (pullback ke EMA mikro atau breakout lilin) yang 100% SEARAH dengan M15 dan M5.
- Jika M1 bertolak belakang dengan M15 atau M5, rekomendasi WAJIB 'HOLD'.

ATURAN REVERSAL / CLOSE:
- Jika ada posisi aktif (BUY/SELL) yang arahnya mulai berbalik berlawanan dengan bias M15/M5 terkini, aksi WAJIB 'CLOSE'.

ATURAN ENTRY SL & TP:
1. entry_tp: Target profit tipis 0.8x hingga 1.2x ATR M1 (sekitar $0.80 - $1.50 pada XAUUSD).
2. entry_sl: Stop loss ketat 1.0x hingga 1.2x ATR M1.
3. Batas logis harga:
   - Jika "BUY" : entry_sl < Ask < entry_tp
   - Jika "SELL": entry_sl > Bid > entry_tp

INSTRUKSI FORMAT OUTPUT (STRICT JSON ONLY):
HANYA kembalikan teks raw JSON valid tanpa tambahan markdown ```json ... ``` atau teks pengantar apa pun.
Format JSON Output Wajib:
{
  "signal": "BUY" | "SELL" | "HOLD" | "CLOSE",
  "confidence": <float 0.0 - 1.0>,
  "m15_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "m5_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "entry_sl": <float>,
  "entry_tp": <float>,
  "reason": "<Penjelasan singkat korelasi M15-M5-M1>"
}"""

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
        
        # Ekstraksi aman (safe float casting) menghindari error saat AI mengirim 'null'
        conf_val = decision.get('confidence')
        confidence = float(conf_val) if conf_val is not None else 0.0
        
        reason = str(decision.get('reason', 'Tidak ada alasan'))
        m15_bias = str(decision.get('m15_bias', 'NEUTRAL')).upper()
        m5_bias = str(decision.get('m5_bias', 'NEUTRAL')).upper()
        
        sl_val = decision.get('entry_sl') if decision.get('entry_sl') is not None else decision.get('sl_price')
        sl_price = float(sl_val) if sl_val is not None else 0.0
        
        tp_val = decision.get('entry_tp') if decision.get('entry_tp') is not None else decision.get('tp_price')
        tp_price = float(tp_val) if tp_val is not None else 0.0
        
        # Simpan kembali format bersih ke dictionary (kompatibilitas multi-field)
        decision['action'] = action
        decision['signal'] = action
        decision['confidence'] = confidence
        decision['m15_bias'] = m15_bias
        decision['m5_bias'] = m5_bias
        decision['reason'] = reason
        decision['sl_price'] = sl_price
        decision['tp_price'] = tp_price
        decision['entry_sl'] = sl_price
        decision['entry_tp'] = tp_price
        
        logger.info(f"[AI MTF] Sinyal: {action} (Conf: {confidence:.2f}) | Bias: M15={m15_bias}, M5={m5_bias} | SL: {sl_price:.2f}, TP: {tp_price:.2f} | Alasan: {reason}")
        
        if confidence < settings.AI_MIN_CONFIDENCE and action in ['BUY', 'SELL']:
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