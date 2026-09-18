import os
import sys
import unittest

# Tambahkan root direktori ke sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.data.market_data import get_adaptive_timeframes, TIMEFRAME_HIERARCHY

class TestAdaptiveMTF(unittest.TestCase):

    def test_timeframe_hierarchy_mapping(self):
        """Memverifikasi pemetaan hierarki adaptif sesuai spesifikasi."""
        expected_mappings = {
            "M1":  {"primary": "M5",  "macro": "M15"},
            "M5":  {"primary": "M15", "macro": "M30"},
            "M15": {"primary": "M30", "macro": "H1"},
            "M30": {"primary": "H1",  "macro": "H4"},
            "H1":  {"primary": "H4",  "macro": "D1"},
        }

        for base_tf, expected in expected_mappings.items():
            result = get_adaptive_timeframes(base_tf)
            self.assertEqual(result["base"], base_tf)
            self.assertEqual(result["primary"], expected["primary"])
            self.assertEqual(result["macro"], expected["macro"])

    def test_case_insensitivity_and_fallback(self):
        """Memverifikasi toleransi case-insensitive (m5, m1) dan fallback default."""
        m5_res = get_adaptive_timeframes("m5")
        self.assertEqual(m5_res["primary"], "M15")
        self.assertEqual(m5_res["macro"], "M30")

        unknown_res = get_adaptive_timeframes("UNKNOWN_TF")
        self.assertEqual(unknown_res["primary"], "M5")
        self.assertEqual(unknown_res["macro"], "M15")

if __name__ == "__main__":
    print("\n" + "=" * 75)
    print("   UNIT TEST: ADAPTIVE MULTI-TIMEFRAME HIERARCHY")
    print("=" * 75)
    unittest.main(verbosity=2)
