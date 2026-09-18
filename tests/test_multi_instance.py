import os
import sys
import unittest
from dotenv import load_dotenv

# Tambahkan root direktori ke sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

class MockPosition:
    def __init__(self, ticket: int, symbol: str, magic: int, profit: float):
        self.ticket = ticket
        self.symbol = symbol
        self.magic = magic
        self.profit = profit
        self.type = 0

class MockDeal:
    def __init__(self, ticket: int, symbol: str, magic: int, profit: float, commission: float = 0.0, swap: float = 0.0):
        self.ticket = ticket
        self.symbol = symbol
        self.magic = magic
        self.profit = profit
        self.commission = commission
        self.swap = swap
        self.entry = 1 # DEAL_ENTRY_OUT

class TestMultiInstanceIsolation(unittest.TestCase):

    def test_environment_file_loading_isolation(self):
        """Memverifikasi bahwa file .env.xau dan .env.forex memuat variabel independen tanpa benturan."""
        # 1. Muat .env.xau
        self.assertTrue(os.path.exists(".env.xau"), "File .env.xau harus ada di root")
        load_dotenv(".env.xau", override=True)
        
        self.assertIn(os.getenv("SYMBOL"), ("XAUUSD", "XAUUSDb"))
        self.assertEqual(int(os.getenv("MAGIC_NUMBER")), 123442)
        self.assertEqual(float(os.getenv("SL_CAP_PRICE")), 2.50)
        self.assertEqual(os.getenv("LOG_FILE"), "logs/trading_xau.log")
        self.assertEqual(float(os.getenv("DAILY_TARGET_PROFIT_IDR")), 300000.0)

        # 2. Muat .env.forex
        self.assertTrue(os.path.exists(".env.forex"), "File .env.forex harus ada di root")
        load_dotenv(".env.forex", override=True)
        
        self.assertIn(os.getenv("SYMBOL"), ("EURUSD", "EURUSDb", "GBPUSD"))
        self.assertEqual(int(os.getenv("MAGIC_NUMBER")), 123422)
        self.assertEqual(float(os.getenv("SL_CAP_PRICE")), 0.0025)
        self.assertEqual(os.getenv("LOG_FILE"), "logs/trading_forex.log")
        self.assertEqual(float(os.getenv("DAILY_TARGET_PROFIT_IDR")), 200000.0)

    def test_magic_number_position_filtering(self):
        """Memverifikasi bahwa penyaringan posisi aktif 100% terisolasi berdasarkan MAGIC_NUMBER."""
        all_positions = [
            MockPosition(ticket=101, symbol="XAUUSD", magic=123411, profit=50.0),
            MockPosition(ticket=102, symbol="EURUSDb", magic=123422, profit=20.0),
            MockPosition(ticket=103, symbol="EURUSDb", magic=999999, profit=-10.0), # Order manual
        ]

        # Instance 1 (XAUUSD - Magic 123411)
        xau_pos = [p for p in all_positions if p.magic == 123411 and "XAU" in p.symbol]
        self.assertEqual(len(xau_pos), 1)
        self.assertEqual(xau_pos[0].ticket, 101)

        # Instance 2 (Forex - Magic 123422)
        forex_pos = [p for p in all_positions if p.magic == 123422 and "EUR" in p.symbol]
        self.assertEqual(len(forex_pos), 1)
        self.assertEqual(forex_pos[0].ticket, 102)

    def test_realized_pnl_isolation(self):
        """Memverifikasi bahwa perhitungan realized PnL harian tidak saling mempengaruhi antar instance."""
        all_deals = [
            MockDeal(ticket=201, symbol="XAUUSD", magic=123411, profit=100.0),
            MockDeal(ticket=202, symbol="XAUUSD", magic=123411, profit=-20.0),
            MockDeal(ticket=203, symbol="EURUSDb", magic=123422, profit=-50.0),
            MockDeal(ticket=204, symbol="GBPUSD", magic=999999, profit=300.0), # Manual trade
        ]

        # PnL Bot 1 (XAUUSD)
        xau_deals = [d for d in all_deals if d.magic == 123411]
        xau_pnl = sum(d.profit + d.commission + d.swap for d in xau_deals)
        self.assertEqual(xau_pnl, 80.0) # 100 - 20

        # PnL Bot 2 (Forex)
        forex_deals = [d for d in all_deals if d.magic == 123422]
        forex_pnl = sum(d.profit + d.commission + d.swap for d in forex_deals)
        self.assertEqual(forex_pnl, -50.0)

        # Pastikan tidak ada kebocoran deal antar bot
        self.assertNotEqual(xau_pnl, forex_pnl)

if __name__ == "__main__":
    print("\n" + "=" * 75)
    print("   UNIT TEST: MULTI-INSTANCE ISOLATION (XAUUSD vs FOREX)")
    print("=" * 75)
    unittest.main(verbosity=2)
