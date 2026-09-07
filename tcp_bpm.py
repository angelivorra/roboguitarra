"""Cliente TCP del tempo de lgptclient (mismo protocolo que pivocoder).

Líneas UTF-8 terminadas en \\n:
  BPM,<ts_ms>,<bpm>  → tempo actual
  SYNC,<ts_ms>       → latido
  START / END / STOP → transporte (opcional)
"""
import socket
import threading
import time

import config


_BACKOFF_INITIAL = 2
_BACKOFF_MAX = 30


class BpmState:
    def __init__(self):
        self._lock = threading.Lock()
        self.bpm = None
        self.connected = False
        self.playing = False
        self._on_bpm = None

    def snapshot(self):
        with self._lock:
            return {
                "bpm": self.bpm,
                "tcp_connected": self.connected,
                "playing": self.playing,
            }

    def set_on_bpm(self, callback):
        self._on_bpm = callback

    def _set_bpm(self, bpm):
        with self._lock:
            self.bpm = float(bpm)
            current = self.bpm
        cb = self._on_bpm
        if cb is not None:
            try:
                cb(current)
            except Exception:
                pass


class _TCPClient:
    def __init__(self, state):
        self._state = state

    def _handle_line(self, line):
        if not line:
            return
        parts = line.split(",")
        tag = parts[0]
        if tag == "BPM" and len(parts) >= 3:
            try:
                self._state._set_bpm(float(parts[2]))
            except ValueError:
                pass
        elif tag in ("START",):
            with self._state._lock:
                self._state.playing = True
        elif tag in ("END", "STOP"):
            with self._state._lock:
                self._state.playing = False

    def _connect_and_read(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect((config.TCP_HOST, config.TCP_PORT))
            sock.settimeout(60)
            with self._state._lock:
                self._state.connected = True
            print(f"[tcp] Conectado a {config.TCP_HOST}:{config.TCP_PORT}")
            buf = ""
            while True:
                chunk = sock.recv(1024).decode("utf-8", errors="replace")
                if not chunk:
                    break
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    self._handle_line(line.strip())

    def run(self):
        backoff = _BACKOFF_INITIAL
        while True:
            try:
                self._connect_and_read()
            except Exception as exc:
                print(f"[tcp] Desconectado: {exc}")
            with self._state._lock:
                self._state.connected = False
            time.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)


_state = None
_lock = threading.Lock()


def current_state():
    if _state is None:
        return {"bpm": None, "tcp_connected": False, "playing": False}
    return _state.snapshot()


def start_tcp_client(on_bpm=None):
    global _state
    with _lock:
        if _state is not None:
            if on_bpm is not None:
                _state.set_on_bpm(on_bpm)
            return _state
        state = BpmState()
        if on_bpm is not None:
            state.set_on_bpm(on_bpm)
        thread = threading.Thread(
            target=_TCPClient(state).run, daemon=True, name="tcp-bpm-client"
        )
        thread.start()
        _state = state
        return state
