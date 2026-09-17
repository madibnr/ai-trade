import MetaTrader5 as mt5
import pandas as pd
from utils.logger import logger

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