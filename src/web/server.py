import os
import sys
import time
import json
import asyncio
import threading
from datetime import datetime
from typing import Dict, Any, List

from utils.logger import logger

# Global in-memory Dashboard State (Thread-Safe via Copy)
dashboard_state: Dict[str, Any] = {
    "account": {
        "balance": 0.0,
        "equity": 0.0,
        "margin_free": 0.0,
        "daily_realized_pnl": 0.0,
        "currency": "USD",
        "circuit_breaker_locked": False
    },
    "market": {
        "symbol": "XAUUSD",
        "timeframe": "M1",
        "current_bid": 0.0,
        "current_ask": 0.0,
        "spread_points": 0,
        "is_connected": False,
        "server_time": "-"
    },
    "ai_status": {
        "primary_tf": "M5",
        "primary_trend": "NEUTRAL",
        "macro_tf": "M15",
        "macro_trend": "NEUTRAL",
        "rsi": 50.0,
        "last_signal": "HOLD",
        "confidence": 0.0,
        "reason_summary": "Menunggu candle tertutup pertama..."
    },
    "active_position": {
        "has_position": False,
        "ticket": 0,
        "type": "-",
        "lot": 0.0,
        "open_price": 0.0,
        "current_sl": 0.0,
        "current_tp": 0.0,
        "floating_pnl_idr": 0.0,
        "stepped_stage": "-"
    },
    "active_trigger": {
        "has_plan": False,
        "action": "-",
        "trigger_price": 0.0,
        "planned_sl": 0.0,
        "planned_tp": 0.0,
        "expires_in_sec": 0
    },
    "recent_events": [
        {
            "time": datetime.now().strftime("%H:%M:%S"),
            "type": "INFO",
            "message": "Dashboard Web diinisialisasi"
        }
    ]
}

_state_lock = threading.Lock()

def update_dashboard_state(category: str, data: Dict[str, Any]) -> None:
    """Memperbarui kategori tertentu dalam dashboard_state secara thread-safe."""
    with _state_lock:
        if category in dashboard_state and isinstance(dashboard_state[category], dict):
            dashboard_state[category].update(data)
        else:
            dashboard_state[category] = data

def add_dashboard_event(event_type: str, message: str) -> None:
    """Menambahkan baris log event krusial (maksimal 10 item terbaru)."""
    with _state_lock:
        events: List[Dict[str, str]] = dashboard_state.get("recent_events", [])
        new_event = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "type": str(event_type).upper(),
            "message": str(message)
        }
        events.insert(0, new_event)
        dashboard_state["recent_events"] = events[:10]

def get_dashboard_state_snapshot() -> Dict[str, Any]:
    """Mengambil salinan snapshot data dashboard terkini."""
    with _state_lock:
        # Mengembalikan deep copy sederhana dari data JSON
        return json.loads(json.dumps(dashboard_state))

def get_current_dashboard_state() -> Dict[str, Any]:
    """Alias untuk mengambil data dashboard terkini."""
    return get_dashboard_state_snapshot()

# Inisialisasi FastAPI & WebSocket Server
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse
    import uvicorn

    app = FastAPI(title="MT5 AI Trading Dashboard")

    @app.get("/", response_class=HTMLResponse)
    async def get_dashboard():
        template_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'web', 'templates', 'index.html'))
        if os.path.exists(template_path):
            with open(template_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        return HTMLResponse(content="<h1>Dashboard Template Not Found</h1>", status_code=404)

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                # Kirim data state ke frontend
                state_data = get_current_dashboard_state()
                await websocket.send_json(state_data)
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            # Client menutup tab/refresh browser secara normal
            pass
        except ConnectionResetError:
            # Menangani [WinError 10054] paksa dari OS Windows
            pass
        except (asyncio.CancelledError, OSError, RuntimeError):
            # Client disconnect atau socket reset
            pass
        except Exception:
            # Log silent atau abaikan saat disconnect
            pass
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

except ImportError as e:
    app = None
    logger.warning(f"[WARNING] FastAPI / Uvicorn belum lengkap: {e}. Dashboard web dinonaktifkan.")

def run_server(host: str = "0.0.0.0", port: int = 8080):
    """Menjalankan server Uvicorn."""
    if app is None:
        logger.warning("[WARNING] Tidak dapat menjalankan dashboard karena dependensi web belum terinstal.")
        return
    # Jalankan uvicorn dengan level logging warning agar tidak mengotori konsol trading
    uvicorn.run(app, host=host, port=port, log_level="warning")

def start_dashboard_thread(host: str = "0.0.0.0", port: int = 8080) -> threading.Thread:
    """
    Menjalankan FastAPI Web Dashboard di background thread (daemon) non-blocking.
    Menjamin zero latency impact pada eksekusi trading bot MT5 utama.
    """
    if app is None:
        return None

    server_thread = threading.Thread(
        target=run_server,
        args=(host, port),
        daemon=True,
        name="WebDashboardThread"
    )
    server_thread.start()
    logger.info(f"[WEB DASHBOARD] Server dashboard aktif di http://localhost:{port} (Thread: Daemon)")
    add_dashboard_event("SYSTEM", f"Server dashboard aktif di port {port}")
    return server_thread
