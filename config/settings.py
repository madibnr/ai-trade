import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def get_env_float(key: str, default: float) -> float:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return float(default)
    try:
        return float(val.strip())
    except ValueError:
        return float(default)

def get_env_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return int(default)
    try:
        return int(val.strip())
    except ValueError:
        return int(default)

def get_env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return bool(default)
    return val.strip().lower() in ("true", "1", "yes", "y")

# MT5 Connection
MT5_LOGIN = get_env_int("MT5_LOGIN", 0)
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")

# Trading Parameters
SYMBOL = os.getenv("SYMBOL", "XAUUSD")
TIMEFRAME_STR = os.getenv("TIMEFRAME", "M15")
MAGIC_NUMBER = get_env_int("MAGIC_NUMBER", 100100)
MAX_OPEN_POSITIONS = get_env_int("MAX_OPEN_POSITIONS", 1) # Maksimal posisi aktif simultan (anti-stacking)

# Risk
RISK_PERCENT = get_env_float("RISK_PERCENT", 1.0)
MAX_SLIPPAGE = get_env_int("MAX_SLIPPAGE", 5) # In points
MAX_LOT = get_env_float("MAX_LOT", 0.01) # Batasan lot maksimal untuk akun kecil
MAX_SPREAD_POINTS = get_env_int("MAX_SPREAD_POINTS", 40) # Batas maksimal spread
MAX_DAILY_LOSS_PERCENT = get_env_float("MAX_DAILY_LOSS_PERCENT", 2.0) # Batas kerugian harian maksimal

# Proteksi Modal Mikro (Nominal Hard Stop)
MAX_LOSS_IDR = get_env_float("MAX_LOSS_IDR", 15000.0)
KURS_USD_IDR = get_env_float("KURS_USD_IDR", 16000.0)

# Auto Break-Even (BEP / Lock Profit)
ENABLE_AUTO_BEP = get_env_bool("ENABLE_AUTO_BEP", True)
BEP_TRIGGER_PROFIT_IDR = get_env_float("BEP_TRIGGER_PROFIT_IDR", 20000.0)
BEP_LOCK_OFFSET_PRICE = get_env_float("BEP_LOCK_OFFSET_PRICE", 0.20)

# Daily Target Profit Lock & Circuit Breaker
ENABLE_DAILY_TARGET_LOCK = get_env_bool("ENABLE_DAILY_TARGET_LOCK", True)
DAILY_TARGET_PROFIT_IDR = get_env_float("DAILY_TARGET_PROFIT_IDR", 500000.0)
DAILY_MAX_LOSS_IDR = get_env_float("DAILY_MAX_LOSS_IDR", 150000.0)

# AI / LLM Configuration
AI_API_BASE_URL = os.getenv("AI_API_BASE_URL", "https://api.openai.com/v1")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL_NAME = os.getenv("AI_MODEL_NAME", "ag/gemini-3.8-flash-low")
AI_MIN_CONFIDENCE = get_env_float("AI_MIN_CONFIDENCE", 0.70)