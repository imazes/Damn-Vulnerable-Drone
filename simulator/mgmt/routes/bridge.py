# simulator/mgmt/routes/telemetry.py
import os
import time
import json
import queue
import threading
import logging
import socket
from typing import Optional, Set, Dict, Any
from typing import Tuple
import requests
from flask import jsonify, Response, stream_with_context, current_app
from . import bp
from .utils import LITE

# python-socketio client (to receive from Companion Computer)
import socketio as sio_client

log = logging.getLogger(__name__)

DEFAULT_COMPANION = f"http://10.13.0.3:3000"

COMPANION_BASE_URL = os.getenv("COMPANION_BASE_URL", DEFAULT_COMPANION)
COMPANION_IO_URL   = os.getenv("COMPANION_IO_URL",  COMPANION_BASE_URL)
COMPANION_NS       = os.getenv("COMPANION_NS", "/")
COMPANION_SIO_PATH = os.getenv("COMPANION_SIO_PATH", "/socket.io")
DEBUG_TELEMETRY    = os.getenv("DEBUG_TELEMETRY", "0").lower() in ("1","true","yes","on")

# ---------- Bridge state ----------
_FORWARDER_STARTED = False
_FORWARDER_LOCK = threading.Lock()

_SUBSCRIBERS: Set[queue.Queue] = set()
_SUB_LOCK = threading.Lock()

_LAST_JSON: Optional[str] = None
_last_position: Optional[Dict[str, Any]] = None
_last_status: Dict[str, Any] = {
    "connected": False,
    "telemetry_status": None,
    "last_msg_ts": None,
    "errors": 0,
    "messages_seen": 0,
    "positions_seen": 0,
    "connect_sid": None,
    "transport": None,
}

def _publish(obj):
    """Broadcast a dict to all SSE subscribers as a JSON string."""
    global _LAST_JSON
    try:
        payload = json.dumps(obj, separators=(",", ":"))
        _LAST_JSON = payload
    except Exception:
        return
    dead = []
    with _SUB_LOCK:
        for q in list(_SUBSCRIBERS):
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            _SUBSCRIBERS.discard(q)

def _update_from_mav(msg: dict) -> bool:
    """Parse a few MAVLink messages for quick map updates."""
    global _last_position
    m = msg.get("message") if isinstance(msg, dict) and "message" in msg else msg
    if not isinstance(m, dict):
        return False

    t = m.get("mavpackettype") or m.get("type") or m.get("msgname")
    try:
        if t == "GLOBAL_POSITION_INT":
            lat = m.get("lat"); lon = m.get("lon")
            alt = m.get("relative_alt", m.get("alt"))
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                _last_position = {
                    "lat": lat / 1e7,
                    "lon": lon / 1e7,
                    "alt": (alt or 0) / 1000.0,
                    "src": "GPI",
                    "ts": time.time(),
                }
                _last_status["positions_seen"] += 1
                return True

        if t == "GPS_RAW_INT":
            lat = m.get("lat"); lon = m.get("lon")
            alt = m.get("alt")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                _last_position = {
                    "lat": lat / 1e7,
                    "lon": lon / 1e7,
                    "alt": (alt or 0) / 1000.0,
                    "src": "GPS",
                    "ts": time.time(),
                }
                _last_status["positions_seen"] += 1
                return True
    except Exception as e:
        log.exception("[bridge] parse error: %s", e)

    return False

def _resolve_debug(hostport: str) -> str:
    """Return 'host:ip:port' for quick sanity checks."""
    try:
        host = hostport.split("://", 1)[-1].split("/", 1)[0]  # host:port
        h, p = (host.split(":")[0], (host.split(":")[1] if ":" in host else ""))
        ip = socket.gethostbyname(h)
        return f"{h}({ip}){':' + p if p else ''}"
    except Exception:
        return hostport

def _http_reachable(url: str) -> Tuple[bool, str]:
    try:
        r = requests.get(url, timeout=3)
        return True, f"{r.status_code}"
    except Exception as e:
        return False, str(e)

def _forward_loop():
    """Connect to Companion Socket.IO; forward every event to SSE clients."""
    global _last_status
    while True:
        try:
            sio = sio_client.Client(
                reconnection=True,
                logger=DEBUG_TELEMETRY,
                engineio_logger=DEBUG_TELEMETRY,
            )

            @sio.event
            def connect():
                _last_status["connected"] = True
                _last_status["connect_sid"] = sio.sid
                _last_status["transport"] = getattr(sio.eio, "transport", None)
                log.info(
                    "[bridge] CONNECTED sid=%s transport=%s ns=%s url=%s path=%s",
                    sio.sid, _last_status["transport"], COMPANION_NS,
                    _resolve_debug(COMPANION_IO_URL), COMPANION_SIO_PATH,
                )
                # publish a tiny meta frame so SSE clients can see life
                _publish({"_meta": {"event": "connect", "sid": sio.sid, "ts": time.time()}})

            @sio.event
            def disconnect():
                _last_status["connected"] = False
                log.warning("[bridge] DISCONNECTED")
                _publish({"_meta": {"event": "disconnect", "ts": time.time()}})

            @sio.on("telemetry_status", namespace=COMPANION_NS)
            def on_status(data):
                _last_status["telemetry_status"] = data.get("isTelemetryRunning") if isinstance(data, dict) else data
                _last_status["last_msg_ts"] = time.time()
                _last_status["messages_seen"] += 1
                _publish({"telemetry_status": _last_status["telemetry_status"]})

            @sio.on("mavlink_message", namespace=COMPANION_NS)
            def on_mavlink(data):
                _last_status["last_msg_ts"] = time.time()
                _last_status["messages_seen"] += 1
                _publish(data)
                _update_from_mav(data)

            # Some servers emit generic 'message'
            @sio.on("message", namespace=COMPANION_NS)
            def on_message(data):
                _last_status["last_msg_ts"] = time.time()
                _last_status["messages_seen"] += 1
                _publish(data)
                _update_from_mav(data)

            # Optional: catch-all handler for debugging (namespaced)
            try:
                @sio.on_any_event
                def _on_any(event, *args):
                    # This exists in JS client; in python-socketio it's available from v5.11+
                    # If present, it'll help prove events are flowing.
                    _last_status["last_msg_ts"] = time.time()
                    _last_status["messages_seen"] += 1
                    if DEBUG_TELEMETRY:
                        log.info("[bridge] on_any_event %s args=%s", event, (args[:1] + ("…",) if len(args) > 1 else args))
            except Exception:
                pass

            # Quick HTTP reachability before we attempt Socket.IO
            ok, why = _http_reachable(f"{COMPANION_BASE_URL}/config")
            log.info("[bridge] reachability %s -> %s", _resolve_debug(COMPANION_BASE_URL), "OK" if ok else why)

            log.info(
                "[bridge] connecting url=%s ns=%s path=%s transports=websocket,polling …",
                _resolve_debug(COMPANION_IO_URL), COMPANION_NS, COMPANION_SIO_PATH
            )
            sio.connect(
                COMPANION_IO_URL,
                transports=["websocket", "polling"],
                namespaces=[COMPANION_NS],
                socketio_path=COMPANION_SIO_PATH.lstrip("/")  # python-socketio expects no leading slash
            )
            sio.wait()
        except Exception as e:
            _last_status["errors"] += 1
            _last_status["connected"] = False
            log.exception("[bridge] error: %s (retry in 2s)", e)
            time.sleep(2.0)

def _start_forwarder_once():
    global _FORWARDER_STARTED
    with _FORWARDER_LOCK:
        if _FORWARDER_STARTED:
            return
        t = threading.Thread(target=_forward_loop, daemon=True)
        t.start()
        _FORWARDER_STARTED = True
        log.info("[bridge] forwarder started")

@bp.before_app_request
def _kick_forwarder():
    _start_forwarder_once()

# ---------- Public helpers used by other modules ----------
def start_companion_telemetry(data: dict):
    url = f"{COMPANION_BASE_URL}/telemetry/start-telemetry"
    try:
        r = requests.post(url, json=data, timeout=10)
        log.info("[telemetry] start -> %s %s", r.status_code, r.text[:200])
    except Exception as e:
        log.error("[telemetry] start error: %s", e)

def stop_companion_telemetry():
    url = f"{COMPANION_BASE_URL}/telemetry/stop-telemetry"
    try:
        r = requests.post(url, timeout=10)
        log.info("[telemetry] stop -> %s %s", r.status_code, r.text[:200])
    except Exception as e:
        log.error("[telemetry] stop error: %s", e)

# ---------- Routes exposed to browser ----------
@bp.route("/telemetry/stream")
def telemetry_stream():
    """SSE endpoint. Emits 'telemetry' events that contain JSON payloads."""
    _start_forwarder_once()
    q: queue.Queue = queue.Queue(maxsize=256)
    with _SUB_LOCK:
        _SUBSCRIBERS.add(q)

    def gen():
        # Send meta snapshot first
        yield "event: telemetry\ndata: " + json.dumps({"_meta": {
            "companion_url": COMPANION_IO_URL,
            "namespace": COMPANION_NS,
            "path": COMPANION_SIO_PATH,
            "connected": _last_status["connected"],
            "messages_seen": _last_status["messages_seen"],
        }}) + "\n\n"
        if _LAST_JSON:
            yield "event: telemetry\ndata: " + _LAST_JSON + "\n\n"
        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield "event: telemetry\ndata: " + msg + "\n\n"
                except queue.Empty:
                    yield ": ping\n\n"  # keep-alive
        finally:
            with _SUB_LOCK:
                _SUBSCRIBERS.discard(q)

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return Response(stream_with_context(gen()), headers=headers)

@bp.route("/telemetry/last")
def telemetry_last():
    return (_LAST_JSON or "{}"), 200, {"Content-Type": "application/json"}

@bp.route("/telemetry/last-position")
def last_position():
    if not _last_position:
        return jsonify({"message": None}), 404
    return jsonify(_last_position)

@bp.route("/telemetry/bridge-status")
def bridge_status():
    return jsonify({
        "companion_base_url": COMPANION_BASE_URL,
        "companion_io_url": COMPANION_IO_URL,
        "namespace": COMPANION_NS,
        "socketio_path": COMPANION_SIO_PATH,
        **_last_status,
        "have_position": bool(_last_position),
        "last_position": _last_position,
    })

@bp.route("/telemetry/restart-bridge", methods=["POST"])
def restart_bridge():
    """(Optional) If you need a manual kick during dev."""
    global _FORWARDER_STARTED
    with _FORWARDER_LOCK:
        _FORWARDER_STARTED = False
    _start_forwarder_once()
    return jsonify({"ok": True})
