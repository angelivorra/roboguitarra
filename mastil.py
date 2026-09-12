"""Monitor de cuerdas leyendo el USB serie del Leonardo (sin reflashear).

El firmware ya imprime al pisar (`DEDO cN ON v=… traste=…`) y responde al
comando `SENS` con ADC, estado y traste de cada cuerda. Abrimos el CDC
desactivando HUPCL para no resetear la placa al conectar.
"""
import os
import re
import termios
import threading
import time
from pathlib import Path


NUM_CUERDAS = 3
STRING_NAMES = ("1ª Mi", "2ª Si", "3ª Sol")
OPEN_NOTES = (64, 59, 55)  # nota al aire por cuerda (mismo que el firmware)

_SENS_INTERVAL = 0.15
_WATCH_TTL = 2.5
_RECONNECT_S = 2.0
BAUD = 115200

_RE_DEDO = re.compile(
    r"DEDO c(\d+) (ON|OFF)\s+v=(\d+)(?:\s+traste=(\d+))?"
)
_RE_CUERDA = re.compile(
    r"^\s*c(\d+) v=(\d+) est=(\S+) traste=(\S+) activa=(\d+) n=(\S+) dedo=(\d+)"
)


def _empty_string():
    return {
        "adc": None,
        "fret": None,
        "finger": False,
        "state": "sin",
        "note": None,
        "source": None,
    }


class MastilMonitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._strings = [_empty_string() for _ in range(NUM_CUERDAS)]
        self.port = None
        self.connected = False
        self.error = None
        self._watch_until = 0.0
        self._fd = None
        self._started = False

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._run, name="mastil-serial", daemon=True).start()

    def watch(self):
        """La UI está mirando: pide un SENS periódico."""
        with self._lock:
            self._watch_until = time.monotonic() + _WATCH_TTL

    def snapshot(self):
        with self._lock:
            return {
                "serial_connected": self.connected,
                "port": self.port,
                "error": self.error,
                "strings": [dict(s) for s in self._strings],
            }

    def apply_midi_notes(self, sounding):
        """Rellena traste por nota MIDI si el serie aún no ha visto esa cuerda."""
        by_ch = {}
        for item in sounding or []:
            ch = int(item.get("ch", 0))
            if 0 <= ch < NUM_CUERDAS:
                by_ch[ch] = int(item.get("note", -1))
        with self._lock:
            for i, open_note in enumerate(OPEN_NOTES):
                if self._strings[i]["source"] == "serial":
                    continue
                note = by_ch.get(i)
                if note is None:
                    if self._strings[i]["source"] == "midi":
                        self._strings[i] = _empty_string()
                    continue
                fret = note - open_note
                self._strings[i].update(
                    {
                        "finger": fret >= 1,
                        "fret": fret if fret >= 1 else None,
                        "note": note,
                        "state": "dedo" if fret >= 1 else "sin",
                        "source": "midi",
                    }
                )

    def _run(self):
        buf = b""
        last_sens = 0.0
        last_try = 0.0
        while True:
            watching = self._is_watching()
            if self._fd is None:
                if not watching:
                    time.sleep(0.2)
                    continue
                now = time.monotonic()
                if now - last_try < _RECONNECT_S:
                    time.sleep(0.1)
                    continue
                last_try = now
                self._open()
                buf = b""
                last_sens = 0.0
                continue

            if watching and time.monotonic() - last_sens >= _SENS_INTERVAL:
                try:
                    os.write(self._fd, b"SENS\n")
                    last_sens = time.monotonic()
                except OSError as exc:
                    self._close(str(exc))
                    continue

            try:
                chunk = os.read(self._fd, 1024)
            except BlockingIOError:
                time.sleep(0.02)
                continue
            except OSError as exc:
                self._close(str(exc))
                continue
            if not chunk:
                time.sleep(0.02)
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    self._handle_line(line)

    def _is_watching(self):
        with self._lock:
            return time.monotonic() < self._watch_until

    def _open(self):
        port = _find_port()
        if not port:
            with self._lock:
                self.connected = False
                self.port = None
                self.error = "No hay puerto serie del Leonardo (/dev/ttyACM*)"
            return
        try:
            fd = _open_cdc(port)
        except OSError as exc:
            with self._lock:
                self.connected = False
                self.port = port
                self.error = str(exc)
            return
        with self._lock:
            self._fd = fd
            self.port = port
            self.connected = True
            self.error = None

    def _close(self, error=None):
        fd = None
        with self._lock:
            fd = self._fd
            self._fd = None
            self.connected = False
            if error:
                self.error = error
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def _handle_line(self, line):
        m = _RE_DEDO.search(line)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < NUM_CUERDAS:
                on = m.group(2) == "ON"
                adc = int(m.group(3))
                fret = int(m.group(4)) if on and m.group(4) else None
                with self._lock:
                    self._strings[idx].update(
                        {
                            "adc": adc,
                            "finger": on,
                            "fret": fret,
                            "state": "dedo" if on else "sin",
                            "source": "serial",
                        }
                    )
            return

        m = _RE_CUERDA.match(line)
        if not m:
            return
        idx = int(m.group(1)) - 1
        if not (0 <= idx < NUM_CUERDAS):
            return
        adc = int(m.group(2))
        state = m.group(3)
        raw_fret = m.group(4)
        fret = int(raw_fret) if raw_fret.isdigit() else None
        note_raw = m.group(6)
        note = int(note_raw) if note_raw.lstrip("-").isdigit() and int(note_raw) >= 0 else None
        finger = m.group(7) == "1" or state in ("dedo", "suelta", "pulsa")
        if not finger:
            fret = None
        with self._lock:
            self._strings[idx].update(
                {
                    "adc": adc,
                    "fret": fret,
                    "finger": finger,
                    "state": state,
                    "note": note,
                    "source": "serial",
                }
            )


def _find_port():
    by_id = Path("/dev/serial/by-id")
    if by_id.is_dir():
        for path in sorted(by_id.iterdir()):
            name = path.name.lower()
            if "arduino" in name or "leonardo" in name or "2341" in name:
                return str(path.resolve())
    for cand in ("/dev/ttyACM0", "/dev/ttyACM1", "/dev/ttyACM2"):
        if Path(cand).exists():
            return cand
    return None


def _open_cdc(port):
    """Abre el CDC a 115200 8N1 sin HUPCL (evita el reset por DTR)."""
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] |= termios.CLOCAL | termios.CREAD | termios.CS8
        attrs[2] &= ~(termios.PARENB | termios.CSTOPB | termios.HUPCL)
        attrs[3] = 0
        attrs[4] = termios.B115200
        attrs[5] = termios.B115200
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        try:
            termios.tcflush(fd, termios.TCIOFLUSH)
        except termios.error:
            pass
    except Exception:
        os.close(fd)
        raise
    return fd


monitor = MastilMonitor()
