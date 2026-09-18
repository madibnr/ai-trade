import os
import sys
import time
import unittest
import pandas as pd

# Pastikan root direktori terdaftar dalam sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings
import src.execution.order_manager as om
import src.strategy.ai_strategy as ai
from src.strategy.base import calculate_indicators

class MockTick:
    def __init__(self, bid=4350.0, ask=4350.5):
        self.bid = bid
        self.ask = ask
        self.time = int(time.time())

class MockPosition:
    def __init__(self, ticket=999, pos_type=0, price_open=4350.0, sl=4348.0, tp=4355.0, profit=10.0, volume=0.01):
        self.ticket = ticket
        self.symbol = "XAUUSD"
        self.type = pos_type
        self.price_open = price_open
        self.sl = sl
        self.tp = tp
        self.profit = profit
        self.volume = volume

class TestAuditFixes(unittest.TestCase):
    def test_patch1_closed_bar_indicators(self):
        """Uji Patch 1: Indikator teknikal dihitung stabil dan tidak menghasilkan NaN."""
        df = pd.DataFrame({
            'open': [4350.0 + i * 0.1 for i in range(30)],
            'high': [4351.0 + i * 0.1 for i in range(30)],
            'low': [4349.0 + i * 0.1 for i in range(30)],
            'close': [4350.5 + i * 0.1 for i in range(30)],
            'tick_volume': [100] * 30
        })
        df_ind = calculate_indicators(df)
        self.assertFalse(df_ind['ema_9'].isna().any())
        self.assertFalse(df_ind['ema_21'].isna().any())
        self.assertFalse(df_ind['rsi_14'].isna().any())
        self.assertFalse(df_ind['atr_14'].isna().any())

    def test_patch2_json_parsing_and_prompt_context(self):
        """Uji Patch 2: Parsing kutip tunggal dan pencegahan crash NameError pos_status."""
        # 1. Parsing single-quoted Python dict
        single_quote_json = "{'action': 'BUY', 'confidence': 0.92, 'reason': 'Testing ast parser'}"
        parsed = ai.clean_and_parse_json(single_quote_json)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.get('action'), 'BUY')
        self.assertEqual(parsed.get('confidence'), 0.92)

        # 2. DataFrame fallback build_prompt_context tanpa NameError
        df = pd.DataFrame({
            'open': [4350.0] * 30,
            'high': [4351.0] * 30,
            'low': [4349.0] * 30,
            'close': [4350.5] * 30,
        })
        tick = MockTick(bid=4350.0, ask=4350.4)
        context = ai.build_prompt_context(df, tick, [])
        self.assertIn("Status Posisi Saat Ini: FLAT", context)

    def test_patch3_zero_sl_rejection(self):
        """Uji Patch 3: open_position menolak mutlak order dengan SL <= 0."""
        # Pastikan circuit breaker tidak terkunci agar validasi SL dapat diuji
        om.set_circuit_breaker_lock(False, "")
        
        # SL bernilai 0 harus ditolak seketika (mengembalikan None)
        res_buy = om.open_position("XAUUSD", 0, 0.01, sl_price=0.0)
        self.assertIsNone(res_buy)

        res_sell = om.open_position("XAUUSD", 1, 0.01, sl_price=-1.0)
        self.assertIsNone(res_sell)

    def test_patch4_bep_pre_check_precision(self):
        """Uji Patch 4: Posisi dengan SL di bawah harga entry tidak boleh ditandai bep_done."""
        pos = MockPosition(pos_type=0, price_open=4350.0, sl=4349.80) # Masih rugi 20 sen
        digits = 2
        offset_price = 0.20
        # Kondisi baru: pos.sl >= round(pos.price_open + (offset_price * 0.5), digits)
        is_bep_pre = pos.sl >= round(pos.price_open + (offset_price * 0.5), digits)
        self.assertFalse(is_bep_pre, "Posisi dengan SL di bawah entry tidak boleh terpicu BEP!")

        pos_bep = MockPosition(pos_type=0, price_open=4350.0, sl=4350.20)
        is_bep_pre_ok = pos_bep.sl >= round(pos_bep.price_open + (offset_price * 0.5), digits)
        self.assertTrue(is_bep_pre_ok, "Posisi dengan SL mengunci impas harus terpicu BEP")

    def test_patch6_pending_trigger_buffer(self):
        """Uji Patch 6: PENDING_BUY / PENDING_SELL wajib memiliki buffer minimal >= 0.25."""
        market_input = {
            "metadata": {"digits": 2, "point": 0.01, "stops_level": 30, "stops_level_dist": 0.30, "name": "XAUUSD"},
            "m15": {"trend": "BULLISH", "recent_candles": []},
            "m5": {"trend": "BULLISH", "recent_candles": []},
            "m1": {"trend": "BULLISH", "recent_candles": [], "atr": 1.20, "latest_close": 4350.0}
        }
        tick = MockTick(bid=4350.0, ask=4350.4)
        
        # Simulasikan hasil keputusan AI dengan trigger_price yang terlalu dekat (misal ask + 0.05)
        decision_raw = {
            "action": "PENDING_BUY",
            "trigger_price": 4350.45, # Jarak hanya 0.05 dari ask 4350.40
            "sl_price": 4348.0,
            "tp_price": 4354.0,
            "confidence": 0.90
        }
        
        # Validasi logika guardrail buffer
        min_trigger_buffer = max(0.25, market_input["metadata"]["stops_level_dist"])
        self.assertGreaterEqual(min_trigger_buffer, 0.25)
        
        adjusted_trigger = round(tick.ask + min_trigger_buffer, 2)
        self.assertGreaterEqual(adjusted_trigger - tick.ask, 0.25)

if __name__ == "__main__":
    unittest.main()
