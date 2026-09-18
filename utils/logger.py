import logging
import os
import sys

def setup_logger(name="MT5Bot", log_file=None):
    if log_file is None:
        log_file = os.getenv("LOG_FILE", "logs/trading_bot.log")
        
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    else:
        os.makedirs("logs", exist_ok=True)
    
    # Memaksa output konsol menggunakan UTF-8 agar mencegah error di terminal Windows
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # Hapus handler lama jika ada (mencegah duplikasi handler / benturan file lock)
    if logger.handlers:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # File handler dengan UTF-8 encoding
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
        
    return logger

logger = setup_logger()