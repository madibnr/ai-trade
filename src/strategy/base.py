import pandas as pd
import numpy as np
from utils.logger import logger

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Menghitung EMA 9, EMA 21, RSI 14, dan ATR 14.
    """
    df = df.copy()
    
    # EMA 9 dan 21
    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    
    # RSI 14 (Wilder's Smoothing)
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    
    rs = avg_gain / avg_loss
    df['rsi_14'] = np.where(avg_loss == 0, 100, 100 - (100 / (1 + rs)))
    
    # ATR 14 (Wilder's Smoothing)
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close = (df['low'] - df['close'].shift(1)).abs()
    
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr_14'] = tr.ewm(alpha=1/14, adjust=False).mean()
    
    return df

def generate_signals(df: pd.DataFrame):
    """
    Menganalisis OHLCV dan mengembalikan sinyal.
    Kondisi menggunakan data tertutup penuh pada candle -2 vs candle -1
    agar terhindar dari sinyal palsu yang belum fixed (repainting).
    """
    if len(df) < 25:
        return {'signal': None, 'sl_dist': 0.0, 'tp_dist': 0.0}
        
    df = calculate_indicators(df)
    
    # Kita bandingkan candle Index -2 (sebelumnya) dgn Index -1 (candle yg baru saja ditutup)
    # Candle yg sedang terbentuk tidak dipakai untuk sinyal krn blm final.
    current = df.iloc[-1]
    prev = df.iloc[-2]
    
    ema9_curr = current['ema_9']
    ema21_curr = current['ema_21']
    ema9_prev = prev['ema_9']
    ema21_prev = prev['ema_21']
    rsi_curr = current['rsi_14']
    atr_curr = current['atr_14']
    
    # Deteksi Crossover
    cross_up = (ema9_prev <= ema21_prev) and (ema9_curr > ema21_curr)
    cross_down = (ema9_prev >= ema21_prev) and (ema9_curr < ema21_curr)
    
    signal = None
    
    # Filter RSI
    if cross_up and (50 < rsi_curr < 70):
        signal = 'BUY'
    elif cross_down and (30 < rsi_curr < 50):
        signal = 'SELL'
        
    # Jarak SL 1.5x ATR, Jarak TP 3x ATR (Risk:Reward 1:2)
    sl_multiplier = 1.5
    tp_multiplier = 3.0
    
    sl_dist = atr_curr * sl_multiplier
    tp_dist = atr_curr * tp_multiplier
    
    if signal:
        logger.info(f"Sinyal {signal} Valid! RSI: {rsi_curr:.1f}, ATR: {atr_curr:.2f} (Candle Time: {current.name})")
        
    return {
        'signal': signal,
        'sl_dist': sl_dist,
        'tp_dist': tp_dist
    }