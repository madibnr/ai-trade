# Template Konfigurasi `.env` Siap Copas (XAUUSD vs FOREX)

File ini berisi template konfigurasi `.env` yang **sudah dioptimasi dan siap langsung disalin-tempel (copy-paste)** ke file `.env` proyek Anda sesuai instrumen yang ingin ditradingkan.

---

## 📋 TEMPLATE 1: XAUUSD (GOLD / EMAS) — SIAP COPAS

Gunakan template ini saat Anda ingin bot bertransaksi di instrumen Emas (XAUUSD). Seluruh parameter telah disesuaikan untuk pergerakan harga emas bernilai tinggi ($1 pergerakan = $1 USD pada 0.01 lot).

```env
# ==============================================================================
# 1. MT5 LOGIN CREDENTIALS
# ==============================================================================
MT5_LOGIN=235256076
MT5_PASSWORD=Test123!
MT5_SERVER=HFMarketsGlobal-Demo4

# ==============================================================================
# 2. TRADING SETTINGS
# ==============================================================================
# Catatan Broker: Jika akun Real/Raw gunakan suffix broker (contoh: XAUUSDb)
SYMBOL=XAUUSD
TIMEFRAME=M1
MAGIC_NUMBER=123412
MAX_OPEN_POSITIONS=1

# Mode Operasional: INTERACTIVE (Menu Pilihan 1/2) | AUTO (Langsung Jalan) | SIGNAL (Notifikasi Saja)
BOT_MODE=INTERACTIVE

# ==============================================================================
# 3. RISK & MONEY MANAGEMENT
# ==============================================================================
RISK_PERCENT=3.0
MAX_SLIPPAGE=30
LOT_SIZE=0.01
MAX_LOT=0.01
MAX_SPREAD_POINTS=40
MAX_DAILY_LOSS_PERCENT=2.0

# Proteksi Risiko Nominal Akun Mikro (Maksimal rugi Rp75.000 per posisi)
MAX_LOSS_IDR=75000.0
KURS_USD_IDR=16000.0

# Guardrail Jarak Stop Loss & Rasio R:R Wajib
SL_CAP_PRICE=2.50
MIN_RR_RATIO=1.5
TARGET_RR_RATIO=2.0

# ==============================================================================
# 4. DYNAMIC STEPPED TRADE MANAGEMENT (KHUSUS EMAS)
# ==============================================================================
# Tahap 1: Auto BEP (Pemicu Rp20.000 setara $1.25 harga emas pada 0.01 lot)
ENABLE_AUTO_BEP=True
BEP_TRIGGER_PROFIT_IDR=20000.0
BEP_LOCK_OFFSET_PRICE=0.60

# Tahap 2: Stagnant Lock (Waktu 45 detik sesuai kecepatan gerak emas)
ENABLE_DYNAMIC_MANAGEMENT=True
STAGNANT_LOCK_TRIGGER_RATIO=0.70
STAGNANT_TIMEOUT_SECONDS=45.0
STAGNANT_LOCK_PROFIT_RATIO=0.60

# Tahap 3: TP Extender / Runner
ENABLE_TP_EXTENDER=True
TP_EXTENDER_TRIGGER_RATIO=0.85
TP_EXTEND_ATR_MULT=1.0
TP_EXTEND_SL_LOCK_RATIO=0.75

# ==============================================================================
# 5. DAILY TARGET & CIRCUIT BREAKER
# ==============================================================================
ENABLE_DAILY_TARGET_LOCK=True
DAILY_TARGET_PROFIT_IDR=300000.0
DAILY_MAX_LOSS_IDR=300000.0

# ==============================================================================
# 6. LOGGING TERPISAH (MULTI-INSTANCE)
# ==============================================================================
LOG_FILE=logs/trading_xau.log

# ==============================================================================
# 7. AI / LLM CONFIGURATION
# ==============================================================================
AI_API_BASE_URL=http://localhost:20128/v1
AI_API_KEY=sk-5a365a86e649efe4-c1zszc-faaa88e0
AI_MODEL_NAME=ag/gemini-3.8-flash-low
AI_MIN_CONFIDENCE=0.85
```

---

## 📋 TEMPLATE 2: FOREX (GBPUSD / EURUSD) — SIAP COPAS

Gunakan template ini saat Anda ingin bot bertransaksi di pasangan mata uang Forex. Parameter telah dioptimasi khusus untuk nilai 1 pip forex ($0.10 USD pada 0.01 lot) agar fitur Auto BEP dan Stagnant Lock bekerja secara efektif dan tidak terbentur toleransi waktu yang terlalu sempit.

```env
# ==============================================================================
# 1. MT5 LOGIN CREDENTIALS
# ==============================================================================
MT5_LOGIN=235256076
MT5_PASSWORD=Test123!
MT5_SERVER=HFMarketsGlobal-Demo4

# ==============================================================================
# 2. TRADING SETTINGS
# ==============================================================================
# Catatan Broker: Sesuaikan simbol (contoh: GBPUSD / EURUSD, atau GBPUSDb / EURUSDb jika akun bonus/raw)
SYMBOL=GBPUSD
TIMEFRAME=M1
MAGIC_NUMBER=123413
MAX_OPEN_POSITIONS=1

# Mode Operasional: INTERACTIVE (Menu Pilihan 1/2) | AUTO (Langsung Jalan) | SIGNAL (Notifikasi Saja)
BOT_MODE=INTERACTIVE

# ==============================================================================
# 3. RISK & MONEY MANAGEMENT (ADAPTIF FOREX)
# ==============================================================================
RISK_PERCENT=3.0
MAX_SLIPPAGE=15
LOT_SIZE=0.02
MAX_LOT=0.02
# Spread Forex rata-rata 5-15 poin (0.5-1.5 pips), batasi maksimal 20 poin
MAX_SPREAD_POINTS=20
MAX_DAILY_LOSS_PERCENT=2.0

# Proteksi Risiko Nominal Akun Mikro (Pada 0.01-0.02 lot forex, SL 10 pips = ~$1.00-$2.00 / Rp16.000-Rp32.000)
MAX_LOSS_IDR=35000.0
KURS_USD_IDR=16000.0

# Guardrail Jarak Stop Loss Forex (0.0025 = 25 pips / 250 poin) & Rasio R:R Wajib
SL_CAP_PRICE=0.0025
MIN_RR_RATIO=1.5
TARGET_RR_RATIO=2.0

# ==============================================================================
# 4. DYNAMIC STEPPED TRADE MANAGEMENT (ADAPTIF FOREX)
# ==============================================================================
# Tahap 1: Auto BEP (Pemicu Rp6.400 setara 4 pips floating profit pada 0.01 lot)
ENABLE_AUTO_BEP=True
BEP_TRIGGER_PROFIT_IDR=6400.0
# Offset penguncian: 0.00015 setara 1.5 pips di atas/bawah harga entry
BEP_LOCK_OFFSET_PRICE=0.00015

# Tahap 2: Stagnant Lock (Waktu 90 detik karena pergerakan forex lebih santai dari emas)
ENABLE_DYNAMIC_MANAGEMENT=True
STAGNANT_LOCK_TRIGGER_RATIO=0.70
STAGNANT_TIMEOUT_SECONDS=90.0
STAGNANT_LOCK_PROFIT_RATIO=0.60

# Tahap 3: TP Extender / Runner
ENABLE_TP_EXTENDER=True
TP_EXTENDER_TRIGGER_RATIO=0.85
TP_EXTEND_ATR_MULT=1.0
TP_EXTEND_SL_LOCK_RATIO=0.75

# ==============================================================================
# 5. DAILY TARGET & CIRCUIT BREAKER
# ==============================================================================
ENABLE_DAILY_TARGET_LOCK=True
DAILY_TARGET_PROFIT_IDR=200000.0
DAILY_MAX_LOSS_IDR=200000.0

# ==============================================================================
# 6. LOGGING TERPISAH (MULTI-INSTANCE)
# ==============================================================================
LOG_FILE=logs/trading_forex.log

# ==============================================================================
# 7. AI / LLM CONFIGURATION
# ==============================================================================
AI_API_BASE_URL=http://localhost:20128/v1
AI_API_KEY=sk-5a365a86e649efe4-c1zszc-faaa88e0
AI_MODEL_NAME=ag/gemini-3.8-flash-low
# Untuk Forex M1 disarankan 0.80 agar frekuensi peluang optimal di sesi London/NY
AI_MIN_CONFIDENCE=0.80
```

---

## ⚡ Cara Cepat Ganti Instrumen dalam 5 Detik

1. Buka file `.env` di VS Code.
2. Buka file `forex-env.md`.
3. Salin salah satu blok konfigurasi di atas (pilih **Template 1 untuk Emas** atau **Template 2 untuk Forex**).
4. Timpa (*overwrite*) seluruh isi file `.env`, lalu simpan (**Ctrl + S**).
5. Jalankan bot:
   ```bash
   python main.py
   ```
   Bot akan otomatis membaca spesifikasi kontrak, digit desimal, spread, dan aturan BEP baru tanpa perlu mengubah kode sumber Python apa pun!

---

## ⏰ Waktu Operasional Terbaik Forex vs Emas

* **XAUUSD (Gold):** Aktif dari pukul **13.00 – 02.00 WIB** (Sesi Eropa & Amerika).
* **GBPUSD / EURUSD (Forex):** Sangat disarankan **HANYA dijalankan pada pukul 14.00 – 22.30 WIB** (tumpang tindih Sesi London & New York). Hindari Sesi Asia (06.00 – 12.30 WIB) karena volatilitas M1 forex cenderung mati suri / *flat*.
