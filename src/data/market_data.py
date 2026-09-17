import MetaTrader5 as mt5
import pandas as pd
from utils.logger import logger
from src.strategy.base import calculate_indicators

def get_symbol_info(symbol):
    """Get specification and status of a symbol."""
    info = mt5.symbol_info(symbol)
    if info is None:
        logger.error(f"Failed to get info for {symbol}")
        return None
        
    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            logger.error(f"Failed to select {symbol}")
            return None
            
    return info

def get_historical_data(symbol, timeframe, count=100):
    """Fetch historical OHLCV data."""
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0:
        logger.error(f"Failed to get historical data for {symbol}, error code: {mt5.last_error()}")
        return pd.DataFrame()
        
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    return df

def get_current_tick(symbol):
    """Fetch current tick (Bid/Ask/Spread)."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"Failed to get tick for {symbol}")
        return None
    return tick

def is_market_open(symbol):
    """Check if market is currently open for the symbol."""
    info = get_symbol_info(symbol)
    if info is None:
        return False
    # trade_mode == mt5.SYMBOL_TRADE_MODE_FULL means trading is allowed
    return info.time > 0 and info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL

def get_multi_timeframe_data(symbol: str) -> dict:
    """
    Menarik data candle dan menghitung indikator teknikal (EMA 9, EMA 21, RSI 14, ATR 14)
    untuk 3 timeframe sekaligus: M15 (Makro), M5 (Struktur), dan M1 (Eksekusi).
    Mengembalikan dictionary terstruktur:
    {
       "m15": { "trend": "...", "ema9": ..., "ema21": ..., "rsi": ..., "atr": ..., "recent_candles": [...] },
       "m5":  { "trend": "...", "ema9": ..., "ema21": ..., "rsi": ..., "atr": ..., "recent_candles": [...] },
       "m1":  { "trend": "...", "ema9": ..., "ema21": ..., "rsi": ..., "atr": ..., "recent_candles": [...] }
    }
    """
    tf_configs = {
        "m15": mt5.TIMEFRAME_M15,
        "m5": mt5.TIMEFRAME_M5,
        "m1": mt5.TIMEFRAME_M1
    }

    mtf_payload = {}

    for tf_key, tf_const in tf_configs.items():
        # Ambil 60 candle untuk pemanasan indikator yang akurat
        df = get_historical_data(symbol, tf_const, count=60)
        if df.empty or len(df) < 25:
            logger.warning(f"[WARNING] Data {tf_key.upper()} tidak cukup untuk kalkulasi indikator.")
            mtf_payload[tf_key] = {
                "trend": "NEUTRAL",
                "ema9": 0.0,
                "ema21": 0.0,
                "rsi": 50.0,
                "atr": 0.0,
                "recent_candles": []
            }
            continue

        df = calculate_indicators(df)
        latest = df.iloc[-1]

        ema9 = round(float(latest['ema_9']), 2)
        ema21 = round(float(latest['ema_21']), 2)
        rsi = round(float(latest['rsi_14']), 2)
        atr = round(float(latest['atr_14']), 2)
        close_price = float(latest['close'])

        # Klasifikasi tren terstruktur
        if close_price > ema21 and ema9 > ema21 and rsi >= 50:
            trend = "BULLISH"
        elif close_price < ema21 and ema9 < ema21 and rsi <= 50:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"

        # Ekstrak 5-10 candle terakhir
        recent_candles = []
        for idx, row in df.iloc[-6:-1].iterrows():
            recent_candles.append({
                "time": str(idx),
                "open": round(float(row['open']), 2),
                "high": round(float(row['high']), 2),
                "low": round(float(row['low']), 2),
                "close": round(float(row['close']), 2),
                "volume": int(row.get('tick_volume', 0))
            })

        mtf_payload[tf_key] = {
            "trend": trend,
            "ema9": ema9,
            "ema21": ema21,
            "rsi": rsi,
            "atr": atr,
            "close": close_price,
            "latest_close": close_price,
            "latest_time": str(df.index[-1]),
            "timestamp": int(df.index[-1].timestamp()),
            "recent_candles": recent_candles
        }

    return mtf_payload