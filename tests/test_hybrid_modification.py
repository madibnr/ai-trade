import os
import sys

# Tambahkan root direktori ke sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings

class MockMT5Position:
    def __init__(self, ticket=2001, symbol="XAUUSD", pos_type=0, price_open=4350.0, sl=4348.0, tp=4354.0):
        self.ticket = ticket
        self.symbol = symbol
        self.type = pos_type # 0 = BUY, 1 = SELL
        self.price_open = price_open
        self.sl = sl
        self.tp = tp

def simulate_ai_modification_guardrail(pos, tick_bid, tick_ask, new_sl, new_tp, digits=2, stops_level_dist=0.30):
    """Simulasi logika guardrail apply_ai_position_modification."""
    old_sl = pos.sl
    old_tp = pos.tp

    if new_tp <= 0.0:
        new_tp = old_tp

    # 1. Guardrail: SL tidak boleh memperlebar risiko
    if pos.type == 0: # BUY
        if old_sl > 0.0 and new_sl < old_sl:
            return False, "REJECTED_WIDEN_RISK", old_sl, old_tp
        if (tick_bid - new_sl) < stops_level_dist:
            return False, "REJECTED_INVALID_STOPS_LEVEL", old_sl, old_tp
        if new_tp > 0.0 and (new_tp - tick_bid) < stops_level_dist:
            return False, "REJECTED_INVALID_TP_LEVEL", old_sl, old_tp

    elif pos.type == 1: # SELL
        if old_sl > 0.0 and new_sl > old_sl:
            return False, "REJECTED_WIDEN_RISK", old_sl, old_tp
        if (new_sl - tick_ask) < stops_level_dist:
            return False, "REJECTED_INVALID_STOPS_LEVEL", old_sl, old_tp
        if new_tp > 0.0 and (tick_ask - new_tp) < stops_level_dist:
            return False, "REJECTED_INVALID_TP_LEVEL", old_sl, old_tp

    # Guardrail R:R untuk new_tp (tidak boleh menurunkan R:R < MIN_RR_RATIO kecuali trailing lock)
    if new_tp != old_tp and new_tp > 0.0:
        sl_for_rr = abs(pos.price_open - new_sl) if new_sl > 0 else abs(pos.price_open - old_sl)
        tp_for_rr = abs(new_tp - pos.price_open)
        is_trailing_lock = (pos.type == 0 and new_sl > pos.price_open) or (pos.type == 1 and new_sl < pos.price_open)
        min_rr = getattr(settings, "MIN_RR_RATIO", 1.5)
        if not is_trailing_lock and sl_for_rr > 0:
            new_rr = tp_for_rr / sl_for_rr
            if new_rr < min_rr:
                return False, "REJECTED_RR_TOO_LOW", old_sl, old_tp

    # 2. Anti-Spam: Cek apakah nilai sudah identik
    new_sl = round(new_sl, digits)
    new_tp = round(new_tp, digits)
    if abs(new_sl - old_sl) < 0.005 and abs(new_tp - old_tp) < 0.005:
        return True, "SKIPPED_IDENTICAL", old_sl, old_tp

    return True, "ACCEPTED", new_sl, new_tp

def test_hybrid_modification():
    print("\n" + "=" * 75)
    print("   UNIT TEST: HYBRID AI POSITION MODIFICATION & GUARDRAILS")
    print("=" * 75)

    # Kasus 1: BUY - AI mencoba memperlebar SL (Ditolak)
    pos_buy = MockMT5Position(ticket=2001, pos_type=0, price_open=4350.0, sl=4349.0, tp=4354.0)
    ok, status, _, _ = simulate_ai_modification_guardrail(pos_buy, tick_bid=4352.0, tick_ask=4352.3, new_sl=4347.0, new_tp=4355.0)
    print(f"[*] Kasus 1 (BUY memperlebar SL 4349 -> 4347): Status = {status}")
    assert not ok and status == "REJECTED_WIDEN_RISK", "Kasus 1 harus ditolak karena memperlebar risiko"

    # Kasus 2: BUY - AI memajukan SL melewati harga pasar (Ditolak broker stops level)
    ok, status, _, _ = simulate_ai_modification_guardrail(pos_buy, tick_bid=4352.0, tick_ask=4352.3, new_sl=4352.1, new_tp=4355.0)
    print(f"[*] Kasus 2 (BUY SL di atas Bid 4352.1 vs 4352.0): Status = {status}")
    assert not ok and status == "REJECTED_INVALID_STOPS_LEVEL", "Kasus 2 harus ditolak karena invalid stops"

    # Kasus 3: BUY - AI memajukan SL secara taktis mengunci profit (Diterima)
    ok, status, final_sl, final_tp = simulate_ai_modification_guardrail(pos_buy, tick_bid=4352.0, tick_ask=4352.3, new_sl=4350.5, new_tp=4354.5)
    print(f"[*] Kasus 3 (BUY SL dinaikkan 4349 -> 4350.5): Status = {status} | SL Baru: {final_sl}")
    assert ok and status == "ACCEPTED" and final_sl == 4350.5, "Kasus 3 harus diterima"

    # Kasus 4: SELL - AI mencoba menaikkan SL (Ditolak memperlebar risiko)
    pos_sell = MockMT5Position(ticket=2002, pos_type=1, price_open=4350.0, sl=4351.0, tp=4346.0)
    ok, status, _, _ = simulate_ai_modification_guardrail(pos_sell, tick_bid=4348.0, tick_ask=4348.3, new_sl=4352.5, new_tp=4345.0)
    print(f"[*] Kasus 4 (SELL menaikkan SL 4351 -> 4352.5): Status = {status}")
    assert not ok and status == "REJECTED_WIDEN_RISK", "Kasus 4 harus ditolak karena memperlebar risiko"

    # Kasus 5: SELL - AI menurunkan SL secara taktis mengunci profit (Diterima)
    ok, status, final_sl, final_tp = simulate_ai_modification_guardrail(pos_sell, tick_bid=4348.0, tick_ask=4348.3, new_sl=4349.5, new_tp=4345.5)
    print(f"[*] Kasus 5 (SELL SL diturunkan 4351 -> 4349.5): Status = {status} | SL Baru: {final_sl}")
    assert ok and status == "ACCEPTED" and final_sl == 4349.5, "Kasus 5 harus diterima"

    # Kasus 6: Anti-Spam - Nilai identik (Dilewati)
    pos_buy.sl = 4350.5
    pos_buy.tp = 4354.5
    ok, status, _, _ = simulate_ai_modification_guardrail(pos_buy, tick_bid=4352.0, tick_ask=4352.3, new_sl=4350.5, new_tp=4354.5)
    print(f"[*] Kasus 6 (Anti-Spam nilai identik): Status = {status}")
    assert ok and status == "SKIPPED_IDENTICAL", "Kasus 6 harus diskip"

    # Kasus 7: BUY - AI memperkecil TP sehingga R:R turun di bawah 1.5 saat posisi belum trailing lock (Ditolak)
    pos_buy_rr = MockMT5Position(ticket=2003, pos_type=0, price_open=4350.0, sl=4348.0, tp=4354.0)
    ok, status, _, _ = simulate_ai_modification_guardrail(pos_buy_rr, tick_bid=4351.0, tick_ask=4351.3, new_sl=4348.5, new_tp=4351.5)
    print(f"[*] Kasus 7 (AI memperkecil TP sehingga R:R 1:1.0 < 1:1.5): Status = {status}")
    assert not ok and status == "REJECTED_RR_TOO_LOW", "Kasus 7 harus ditolak karena R:R < 1.5"

    print("\n" + "=" * 75)
    print("HASIL: SELURUH PENGUJIAN GUARDRAIL HYBRID MODIFICATION BERHASIL LULUS 100% [PASS]")
    print("=" * 75 + "\n")

if __name__ == "__main__":
    test_hybrid_modification()
