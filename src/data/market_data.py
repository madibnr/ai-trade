import MetaTrader5 as mt5
import pandas as pd
from utils.logger import logger
from src.strategy.base import calculate_indicators

TIMEFRAME_HIERARCHY = {
    "M1":  {"primary": "M5",  "macro": "M15"},
    "M5":  {"primary": "M15", "macro": "M30"},
    "M15": {"primary": "M30", "macro": "H1"},
    "M30": {"primary": "H1",  "macro": "H4"},
    "H1":  {"primary": "H4",  "macro": "D1"},
}

TF_CONST_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": getattr(mt5, "TIMEFRAME_M30", 30),
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

def get_adaptive_timeframes(base_tf: str) -> dict:
    """Mengembalikan dictionary berisi base, primary, dan macro timeframe."""
    mapping = TIMEFRAME_HIERARCHY.get(base_tf.upper(), {"primary": "M5", "macro": "M15"})
    return {
        "base": base_tf.upper(),
        "primary": mapping["primary"],
        "macro": mapping["macro"]
    }

def get_symbol_info(symbol: str):
    """
    Get specification and status of a symbol from MT5.
    Ensures symbol is selected in Market Watch.
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        logger.error(f"[ERROR] Gagal mendapatkan info spesifikasi untuk {symbol}")
        return None
        
    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            logger.error(f"[ERROR] Gagal mengaktifkan {symbol} di Market Watch MT5")
            return None
            
    return info

def get_symbol_metadata(symbol: str) -> dict:
    """
    Menarik metadata spesifikasi instrumen secara real-time dari MT5.
    Mendukung adaptasi dinamis untuk berbagai kelas aset:
    - Logam Mulia (XAUUSD, XAGUSD, dll.)
    - Pasangan Mata Uang Forex (EURUSD, GBPUSD, USDJPY, dll.)
    - Indeks & Kripto
    """
    info = get_symbol_info(symbol)
    if info is None:
        # Fallback default adaptif berdasarkan nama simbol
        is_gold_symbol = "XAU" in symbol.upper() or "GOLD" in symbol.upper()
        digits = 2 if is_gold_symbol else 5
        point = 0.01 if is_gold_symbol else 0.00001
        contract_size = 100.0 if is_gold_symbol else 100000.0
        stops_level = 30
        return {
            "name": symbol,
            "digits": digits,
            "point": point,
            "contract_size": contract_size,
            "trade_tick_value": 1.0,
            "trade_tick_size": point,
            "stops_level": stops_level,
            "stops_level_dist": stops_level * point,
            "spread": 20,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "is_forex": not is_gold_symbol,
            "currency_profit": "USD",
            "currency_base": "",
            "currency_margin": ""
        }

    digits = info.digits
    point = info.point
    stops_level = info.trade_stops_level
    stops_level_dist = max(stops_level * point, point)
    contract_size = info.trade_contract_size if info.trade_contract_size > 0 else (100.0 if ("XAU" in symbol.upper() or "GOLD" in symbol.upper()) else 100000.0)
    is_forex = digits >= 3 and contract_size >= 10000

    return {
        "name": info.name,
        "digits": digits,
        "point": point,
        "contract_size": contract_size,
        "trade_tick_value": info.trade_tick_value if info.trade_tick_value > 0 else 1.0,
        "trade_tick_size": info.trade_tick_size,
        "stops_level": stops_level,
        "stops_level_dist": stops_level_dist,
        "spread": info.spread,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
        "is_forex": is_forex,
        "currency_profit": getattr(info, "currency_profit", "USD"),
        "currency_base": getattr(info, "currency_base", ""),
        "currency_margin": getattr(info, "currency_margin", "")
    }

def get_historical_data(symbol: str, timeframe: int, count: int = 100) -> pd.DataFrame:
    """Fetch historical OHLCV data."""
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0:
        logger.error(f"[ERROR] Gagal mengambil data historis {symbol}, error code: {mt5.last_error()}")
        return pd.DataFrame()
        
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    return df

def get_current_tick(symbol: str):
    """Fetch current tick (Bid/Ask/Spread)."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"[ERROR] Gagal mendapatkan harga tick terkini untuk {symbol}")
        return None
    return tick

def is_market_open(symbol: str) -> bool:
    """
    Memeriksa apakah pasar untuk simbol terkait sedang aktif trading.
    Pasar dianggap TUTUP HANYA jika info simbol tidak ditemukan
    atau trade_mode berada dalam status DISABLED.
    """
    info = get_symbol_info(symbol)
    if info is None:
        return False
    if info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
        return False
    return True

def get_multi_timeframe_data(symbol: str, base_tf: str = None) -> dict:
    """
    Menarik data candle dan menghitung indikator teknikal (EMA 9, EMA 21, RSI 14, ATR 14)
    secara adaptif untuk 3 timeframe berdasarkan hierarki konfigurasi:
    - Base Timeframe: pemicu eksekusi dan SL ATR (misal M1)
    - Primary Timeframe: pengambil keputusan utama (misal M5)
    - Macro Timeframe: referensi struktural mayor (misal M15)
    Menggunakan presisi desimal dinamis (digits) sesuai spesifikasi instrumen MT5.
    """
    if base_tf is None:
        base_tf = getattr(settings, "TIMEFRAME_STR", "M1")

    metadata = get_symbol_metadata(symbol)
    digits = metadata["digits"]

    hierarchy = get_adaptive_timeframes(base_tf)
    base_str = hierarchy["base"]
    primary_str = hierarchy["primary"]
    macro_str = hierarchy["macro"]

    tf_configs = {
        "base": (base_str, TF_CONST_MAP.get(base_str, mt5.TIMEFRAME_M1)),
        "primary": (primary_str, TF_CONST_MAP.get(primary_str, mt5.TIMEFRAME_M5)),
        "macro": (macro_str, TF_CONST_MAP.get(macro_str, mt5.TIMEFRAME_M15))
    }

    mtf_payload = {
        "metadata": metadata,
        "hierarchy": hierarchy
    }

    for role, (tf_name, tf_const) in tf_configs.items():
        # Ambil 60 candle untuk pemanasan indikator yang akurat
        df = get_historical_data(symbol, tf_const, count=60)
        if df.empty or len(df) < 25:
            logger.warning(f"[WARNING] Data {tf_name} ({role}) tidak cukup untuk kalkulasi indikator.")
            role_data = {
                "timeframe": tf_name,
                "role": role,
                "trend": "NEUTRAL",
                "ema9": 0.0,
                "ema21": 0.0,
                "rsi": 50.0,
                "atr": 0.0,
                "close": 0.0,
                "latest_close": 0.0,
                "latest_time": "-",
                "timestamp": 0,
                "recent_candles": []
            }
            mtf_payload[role] = role_data
            mtf_payload[tf_name.lower()] = role_data
            continue

        df = calculate_indicators(df)
        # Ambil lilin terakhir yang SUDAH TERTUTUP PENUH (Index -2) agar indikator tidak repainting
        closed_bar = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]

        ema9 = round(float(closed_bar['ema_9']), digits)
        ema21 = round(float(closed_bar['ema_21']), digits)
        rsi = round(float(closed_bar['rsi_14']), 2)
        atr = round(float(closed_bar['atr_14']), digits)
        close_price = round(float(closed_bar['close']), digits)

        # Klasifikasi tren terstruktur berbasis lilin tertutup terkonfirmasi
        if close_price > ema21 and ema9 > ema21 and rsi >= 50:
            trend = "BULLISH"
        elif close_price < ema21 and ema9 < ema21 and rsi <= 50:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"

        # Ekstrak 5 candle terakhir yang diformat presisi sesuai digits
        recent_candles = []
        for idx, row in df.iloc[-6:-1].iterrows():
            recent_candles.append({
                "time": str(idx),
                "open": round(float(row['open']), digits),
                "high": round(float(row['high']), digits),
                "low": round(float(row['low']), digits),
                "close": round(float(row['close']), digits),
                "volume": int(row.get('tick_volume', 0))
            })

        role_data = {
            "timeframe": tf_name,
            "role": role,
            "trend": trend,
            "ema9": ema9,
            "ema21": ema21,
            "rsi": rsi,
            "atr": atr,
            "close": close_price,
            "latest_close": close_price,
            "latest_time": str(df.index[-2] if len(df) >= 2 else df.index[-1]),
            "timestamp": int((df.index[-2] if len(df) >= 2 else df.index[-1]).timestamp()),
            "recent_candles": recent_candles
        }
        mtf_payload[role] = role_data
        mtf_payload[tf_name.lower()] = role_data

    return mtf_payload
