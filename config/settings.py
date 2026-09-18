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
MT5_PATH = os.getenv("MT5_PATH", "")
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
LOT_SIZE = get_env_float("LOT_SIZE", 0.01) # Lot size dasar / referensi
MAX_LOT = get_env_float("MAX_LOT", LOT_SIZE) # Batasan lot maksimal untuk akun kecil
MAX_SPREAD_POINTS = get_env_int("MAX_SPREAD_POINTS", 40) # Batas maksimal spread
MAX_DAILY_LOSS_PERCENT = get_env_float("MAX_DAILY_LOSS_PERCENT", 2.0) # Batas kerugian harian maksimal
SL_CAP_PRICE = get_env_float("SL_CAP_PRICE", 2.50) # Batas maksimal jarak Stop Loss (misal 2.50 untuk Gold, 0.0025 untuk Forex)
MIN_RR_RATIO = get_env_float("MIN_RR_RATIO", 1.5) # Batas bawah mutlak rasio R:R (di bawah 1.5 ditolak)
TARGET_RR_RATIO = get_env_float("TARGET_RR_RATIO", 2.0) # Target ideal rasio R:R

# Logging Configuration
LOG_FILE = os.getenv("LOG_FILE", "logs/trading_bot.log")

# Proteksi Modal Mikro (Nominal Hard Stop)
MAX_LOSS_IDR = get_env_float("MAX_LOSS_IDR", 15000.0)
KURS_USD_IDR = get_env_float("KURS_USD_IDR", 16000.0)

# Auto Break-Even (BEP / Lock Profit)
ENABLE_AUTO_BEP = get_env_bool("ENABLE_AUTO_BEP", True)
BEP_TRIGGER_PROFIT_IDR = get_env_float("BEP_TRIGGER_PROFIT_IDR", 20000.0)
BEP_LOCK_OFFSET_PRICE = get_env_float("BEP_LOCK_OFFSET_PRICE", 0.20)

# Dynamic Stepped Trade Management (BEP, Stagnant Lock, TP Extender)
ENABLE_DYNAMIC_MANAGEMENT = get_env_bool("ENABLE_DYNAMIC_MANAGEMENT", True)
STAGNANT_LOCK_TRIGGER_RATIO = get_env_float("STAGNANT_LOCK_TRIGGER_RATIO", 0.70)
STAGNANT_TIMEOUT_SECONDS = get_env_float("STAGNANT_TIMEOUT_SECONDS", 45.0)
STAGNANT_LOCK_PROFIT_RATIO = get_env_float("STAGNANT_LOCK_PROFIT_RATIO", 0.60)
ENABLE_TP_EXTENDER = get_env_bool("ENABLE_TP_EXTENDER", True)
TP_EXTENDER_TRIGGER_RATIO = get_env_float("TP_EXTENDER_TRIGGER_RATIO", 0.85)
TP_EXTEND_ATR_MULT = get_env_float("TP_EXTEND_ATR_MULT", 1.0)
TP_EXTEND_SL_LOCK_RATIO = get_env_float("TP_EXTEND_SL_LOCK_RATIO", 0.75)

# Daily Target Profit Lock & Circuit Breaker
ENABLE_DAILY_TARGET_LOCK = get_env_bool("ENABLE_DAILY_TARGET_LOCK", True)
DAILY_TARGET_PROFIT_IDR = get_env_float("DAILY_TARGET_PROFIT_IDR", 500000.0)
DAILY_MAX_LOSS_IDR = get_env_float("DAILY_MAX_LOSS_IDR", 150000.0)

# Operational Mode (INTERACTIVE, AUTO, SIGNAL)
BOT_MODE = os.getenv("BOT_MODE", "INTERACTIVE").strip().upper()

# Web Dashboard Configuration (Real-Time Monitoring)
ENABLE_WEB_DASHBOARD = get_env_bool("ENABLE_WEB_DASHBOARD", True)
WEB_PORT = get_env_int("WEB_PORT", 8080)

# AI / LLM Configuration
AI_API_BASE_URL = os.getenv("AI_API_BASE_URL", "https://api.openai.com/v1")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL_NAME = os.getenv("AI_MODEL_NAME", "ag/gemini-3.8-flash-low")
AI_MIN_CONFIDENCE = get_env_float("AI_MIN_CONFIDENCE", 0.70)