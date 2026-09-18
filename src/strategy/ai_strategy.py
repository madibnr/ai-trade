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

def format_candles_summary(df: pd.DataFrame) -> tuple:
    """Helper untuk merangkum list candle menjadi baris teks terformat dan indikator terakhir."""
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
    
    return latest, candles_str

def build_prompt_context(mtf_data: Dict[str, pd.DataFrame], tick: Any, active_positions: list) -> str:
    """
    Menyusun ringkasan kondisi pasar saat ini untuk diumpankan ke model AI,
    mencakup timeframe M1 dan didukung oleh M5.
    """
    df_m1 = mtf_data.get('m1')
    df_m5 = mtf_data.get('m5')
    
    latest_m1, candles_m1_str = format_candles_summary(df_m1)
    latest_m5, candles_m5_str = format_candles_summary(df_m5)
    
    pos_status = "FLAT (Tidak ada posisi terbuka)"
    if active_positions:
        pos = active_positions[0]
        pos_type_str = "BUY" if pos.type == 0 else "SELL"
        pos_status = f"{pos_type_str} terbuka di harga {pos.price_open}, Lot: {pos.volume}, Profit: {pos.profit}"
    
    context = f"""Status Posisi Saat Ini: {pos_status}
Harga Pasar Saat Ini: Bid = {tick.bid:.2f}, Ask = {tick.ask:.2f}, Spread = {tick.ask - tick.bid:.2f}

=== TIMEFRAME M5 (Struktur & Tren Pendukung) ===
Indikator Terkini (Candle Terakhir M5):
- EMA 9: {latest_m5['ema_9']:.2f}
- EMA 21: {latest_m5['ema_21']:.2f}
- RSI 14: {latest_m5['rsi_14']:.2f}

Data 5 Candle Terakhir M5 (OHLC):
{candles_m5_str}

=== TIMEFRAME M1 (Eksekusi Momentum) ===
Indikator Terkini (Candle Terakhir M1):
- EMA 9: {latest_m1['ema_9']:.2f}
- EMA 21: {latest_m1['ema_21']:.2f}
- RSI 14: {latest_m1['rsi_14']:.2f}
- ATR 14: {latest_m1['atr_14']:.2f}

Data 5 Candle Terakhir M1 (OHLC):
{candles_m1_str}"""
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

def get_ai_decision(mtf_data: Dict[str, pd.DataFrame], tick: Any, active_positions: list) -> Dict[str, Any]:
    """
    Mengirimkan konteks ke REST API LLM dan mengurai respons berformat JSON.
    Fallback ke 'HOLD' jika terjadi error atau timeout.
    """
    fallback_response = {
        "action": "HOLD",
        "confidence": 0.0,
        "sl_price": 0.0,
        "tp_price": 0.0,
        "reason": "Fallback default karena error atau sinyal lemah"
    }

    context_prompt = build_prompt_context(mtf_data, tick, active_positions)
    
    system_prompt = """Anda adalah Aggressive Scalping Trader (M1/M5) yang ahli memanfaatkan pergerakan momentum mikro secara cepat (quick in, quick out) pada instrumen XAUUSD dan Forex.
Tugas Anda adalah memberikan keputusan trading instan dalam format raw JSON berdasarkan dua timeframe (M5 sebagai pendukung arah struktural, M1 sebagai pemicu eksekusi).

INSTRUKSI PENTING (STRICT OUTPUT RULE):
- HANYA kembalikan teks raw JSON yang valid.
- DILARANG KERAS menggunakan markdown formatting (JANGAN gunakan ``` atau ```json).
- DILARANG menyertakan teks pengantar, penutup, salam, atau penjelasan di luar objek JSON.
- Karakter pertama dari respons Anda HARUS berupa "{" dan karakter terakhir HARUS berupa "}".

ATURAN STRATEGI SCALPING (AGRESIF & RESPONSIF):
1. Keselarasan Timeframe (Dual Timeframe): Konfirmasi bahwa momentum di M1 searah dengan struktur tren yang ditunjukkan oleh M5 (contoh: jika EMA9 > EMA21 di M5 dan M1 menunjukkan dorongan candle bullish, maka BUY).
2. KURANGI STATUS HOLD: Selalu berikan rekomendasi "BUY" atau "SELL" selama ada bias arah mikro sekecil apa pun yang terkonfirmasi oleh timeframe M5. Hanya keluarkan "HOLD" jika spread sedang melonjak ekstrem di atas rata-rata, pasar benar-benar stagnan tanpa pergerakan, atau arah M1 bertolak belakang ekstrem dengan M5.
3. TARGET PROFIT TIPIS (Quick Out): Jarak Take Profit (TP) wajib diatur sangat dekat antara 0.8x hingga 1.2x ATR dari timeframe M1. (Contoh untuk XAUUSD berkisar antara $0.80 - $1.50; EURUSD antara 4 - 8 pips).
4. STOP LOSS KETAT: Jarak Stop Loss (SL) disiplin diatur antara 1.0x hingga 1.2x ATR dari timeframe M1. 
5. REVERSAL CEPAT: Jika ada posisi aktif (BUY/SELL) yang mulai berlawanan arah dengan momentum terkini di candle terakhir, segera keluarkan action = "CLOSE" tanpa ragu.
6. BATAS LOGIS HARGA:
   - Jika "BUY": sl_price < ask_price < tp_price
   - Jika "SELL": sl_price > bid_price > tp_price

CONTOH FORMAT OUTPUT:
{
  "action": "BUY",
  "confidence": 0.85,
  "sl_price": 2750.00,
  "tp_price": 2752.50,
  "reason": "Momentum bullish agresif di M1 sejalan dengan tren struktur M5, target scalping 1x ATR M1."
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

    logger.info(f"[INFO] Meminta analisis dari AI ke endpoint: {url}")
    
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
        
        action = str(decision.get('action', 'HOLD')).upper()
        
        # Ekstraksi aman (safe float casting) menghindari error saat AI mengirim 'null'
        conf_val = decision.get('confidence')
        confidence = float(conf_val) if conf_val is not None else 0.0
        
        reason = str(decision.get('reason', 'Tidak ada alasan'))
        
        sl_val = decision.get('sl_price')
        sl_price = float(sl_val) if sl_val is not None else 0.0
        
        tp_val = decision.get('tp_price')
        tp_price = float(tp_val) if tp_val is not None else 0.0
        
        # Simpan kembali format bersih ke dictionary
        decision['action'] = action
        decision['confidence'] = confidence
        decision['reason'] = reason
        decision['sl_price'] = sl_price
        decision['tp_price'] = tp_price
        
        logger.info(f"[AI] Rekomendasi: {action} (Conf: {confidence:.2f}) | Alasan: {reason}")
        
        if confidence < settings.AI_MIN_CONFIDENCE and action in ['BUY', 'SELL']:
            logger.info(f"[AI] Sinyal {action} diabaikan karena confidence ({confidence:.2f}) < Minimum ({settings.AI_MIN_CONFIDENCE})")
            decision['action'] = 'HOLD'
            
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