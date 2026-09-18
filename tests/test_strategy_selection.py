import os
import sys
import unittest

# Tambahkan root direktori ke sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings
import src.execution.order_manager as om

class MockTick:
    def __init__(self, bid=4350.0, ask=4350.5):
        self.bid = bid
        self.ask = ask

class TestStrategySelectionAndStraddle(unittest.TestCase):

    def setUp(self):
        om.reset_trigger_plan()

    def test_create_straddle_plans_both_directions(self):
        """Strategi 2: Memverifikasi pembuatan jebakan ganda mekanis (BUY & SELL)."""
        mtf_data = {
            "metadata": {"digits": 2, "point": 0.01, "stops_level_dist": 0.30, "is_forex": False},
            "hierarchy": {"base": "M1", "primary": "M5", "macro": "M15"},
            "base": {
                "atr": 1.20,
                "recent_candles": [
                    {"open": 4349.0, "high": 4352.0, "low": 4348.0, "close": 4350.0, "volume": 100}
                ]
            }
        }
        tick = MockTick(bid=4350.0, ask=4350.5)

        plans = om.create_straddle_plans("XAUUSD", mtf_data, tick, bias="BOTH")
        self.assertEqual(len(plans), 2, "Harus membuat 2 rencana trap (BUY dan SELL)")
        
        buy_plan = plans[0]
        sell_plan = plans[1]

        self.assertEqual(buy_plan["action"], "BUY")
        self.assertEqual(sell_plan["action"], "SELL")
        self.assertTrue(buy_plan["trigger_price"] > tick.ask)
        self.assertTrue(sell_plan["trigger_price"] < tick.bid)
        self.assertTrue(buy_plan["tp_price"] > buy_plan["trigger_price"])
        self.assertTrue(sell_plan["tp_price"] < sell_plan["trigger_price"])

    def test_create_straddle_plans_hybrid_directional(self):
        """Strategi 3: Memverifikasi pembuatan jebakan satu arah sesuai bias AI."""
        mtf_data = {
            "metadata": {"digits": 2, "point": 0.01, "stops_level_dist": 0.30, "is_forex": False},
            "hierarchy": {"base": "M1", "primary": "M5", "macro": "M15"},
            "base": {
                "atr": 1.20,
                "recent_candles": [
                    {"open": 4349.0, "high": 4352.0, "low": 4348.0, "close": 4350.0, "volume": 100}
                ]
            }
        }
        tick = MockTick(bid=4350.0, ask=4350.5)

        # Kasus Bullish: HANYA pasang BUY trap
        bull_plans = om.create_straddle_plans("XAUUSD", mtf_data, tick, bias="BULLISH")
        self.assertEqual(len(bull_plans), 1)
        self.assertEqual(bull_plans[0]["action"], "BUY")

        # Kasus Bearish: HANYA pasang SELL trap
        bear_plans = om.create_straddle_plans("XAUUSD", mtf_data, tick, bias="BEARISH")
        self.assertEqual(len(bear_plans), 1)
        self.assertEqual(bear_plans[0]["action"], "SELL")

        # Kasus Neutral: TIDAK pasang trap (HOLD / Zero Risk)
        neutral_plans = om.create_straddle_plans("XAUUSD", mtf_data, tick, bias="NEUTRAL")
        self.assertEqual(len(neutral_plans), 0, "Bias Neutral tidak boleh memasang trap")

if __name__ == "__main__":
    print("\n" + "=" * 75)
    print("   UNIT TEST: MODULAR STRATEGY SELECTION & STRADDLE TRAP LOGIC")
    print("=" * 75)
    unittest.main(verbosity=2)
