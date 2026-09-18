import os
import sys
import unittest

# Tambahkan root direktori ke sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.web.server import (
    dashboard_state,
    update_dashboard_state,
    add_dashboard_event,
    get_dashboard_state_snapshot,
    get_current_dashboard_state
)

class TestWebDashboard(unittest.TestCase):

    def test_get_current_dashboard_state(self):
        """Memverifikasi fungsi get_current_dashboard_state mengembalikan data yang identik."""
        state = get_current_dashboard_state()
        self.assertIn("account", state)
        self.assertIn("market", state)
        self.assertIn("ai_status", state)
        self.assertIn("active_position", state)
        self.assertIn("active_trigger", state)
        self.assertIn("recent_events", state)

    def test_dashboard_state_update(self):
        """Memverifikasi pembaruan state thread-safe pada kategori account & market."""
        update_dashboard_state("account", {
            "balance": 500000.0,
            "equity": 520000.0,
            "daily_realized_pnl": 20000.0,
            "circuit_breaker_locked": False
        })
        snap = get_dashboard_state_snapshot()
        self.assertEqual(snap["account"]["balance"], 500000.0)
        self.assertEqual(snap["account"]["equity"], 520000.0)
        self.assertEqual(snap["account"]["daily_realized_pnl"], 20000.0)
        self.assertFalse(snap["account"]["circuit_breaker_locked"])

    def test_dashboard_event_logging(self):
        """Memverifikasi pencatatan event feed dan batasan maksimal 10 item."""
        for i in range(15):
            add_dashboard_event("ORDER", f"Test Event {i}")
            
        snap = get_dashboard_state_snapshot()
        events = snap.get("recent_events", [])
        self.assertLessEqual(len(events), 10, "Event feed harus dibatasi maksimal 10 item")
        self.assertEqual(events[0]["message"], "Test Event 14", "Event terbaru harus berada di indeks pertama")

    def test_html_template_exists(self):
        """Memverifikasi bahwa file single-file HTML/JS dashboard tersedia di web/templates/index.html."""
        template_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'web', 'templates', 'index.html'))
        self.assertTrue(os.path.exists(template_path), "File web/templates/index.html harus ada")
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("MT5 AI QUANTITATIVE BOT", content)
            self.assertIn("tailwindcss", content)
            self.assertIn("WebSocket", content)

if __name__ == "__main__":
    print("\n" + "=" * 75)
    print("   UNIT TEST: REAL-TIME WEB DASHBOARD & STATE DISPATCHER")
    print("=" * 75)
    unittest.main(verbosity=2)
