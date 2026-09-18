import MetaTrader5 as mt5
import time
from utils.logger import logger
from config import settings

def initialize_mt5():
    """Initializes connection to MT5 terminal."""
    logger.info("Initializing MT5 connection...")
    
    if not mt5.initialize(path=settings.MT5_PATH):
        logger.error(f"initialize() failed, error code: {mt5.last_error()}")
        return False
        
    # Attempt login
    if settings.MT5_LOGIN and settings.MT5_PASSWORD and settings.MT5_SERVER:
        authorized = mt5.login(
            settings.MT5_LOGIN, 
            password=settings.MT5_PASSWORD, 
            server=settings.MT5_SERVER
        )
        if not authorized:
            logger.error(f"failed to connect to trade account, error code: {mt5.last_error()}")
            return False
            
    logger.info(f"Connected to MT5 successfully! Account: {mt5.account_info().login}")
    return True

def shutdown_mt5():
    """Gracefully shuts down MT5 connection."""
    logger.info("Shutting down MT5 connection...")
    mt5.shutdown()