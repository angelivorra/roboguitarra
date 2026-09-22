"""Arpegiador sincronizado con el BPM de la canción.

Botón 2 → modo mayor  (si ya estaba en mayor → nota normal)
Botón 3 → modo menor  (si ya estaba en menor → nota normal)
Cambiar de mayor a menor (o viceversa) cambia el modo sin parar el hilo.
"""
import random
import threading
import time

MAJOR_INTERVALS = [0, 4, 7, 12, 16, 19, 24]
MINOR_INTERVALS = [0, 3, 7, 12, 15, 19, 24]

ARP_CHANNEL = 3
DEFAULT_BPM = 180.0


class Arpeggiator:
    def __init__(self):
        self._lock = threading.Lock()
        self._active = False
        self._mode = "note"   # "note" | "major" | "minor"
        self._engine = None

    def bind(self, engine):
        self._engine = engine

    @property
    def active(self):
        with self._lock:
            return self._active

    @property
    def mode(self):
        with self._lock:
            return self._mode

    def set_mode(self, requested):
        """Activa 'major' o 'minor'. Si ya estaba en ese modo → vuelve a 'note'."""
        root = self._current_root()  # fuera del lock para evitar deadlock
        with self._lock:
            if self._mode == requested:
                self._active = False
                self._mode = "note"
                return
            was_active = self._active
            self._active = True
            self._mode = requested
        if not was_active:
            threading.Thread(target=self._run, args=(root,), daemon=True, name="arp").start()
        # Si ya estaba activo (cambio de modo), el hilo recoge _mode en el próximo ciclo

    # ----------------------------------------------------------------- interno

    def _current_root(self):
        eng = self._engine
        if eng is None:
            return None
        sounding = eng.get_state().get("sounding") or []
        notes = [
            s["note"] for s in sounding
            if s.get("note") is not None and s.get("ch") != ARP_CHANNEL
        ]
        return min(notes) if notes else None

    def _bpm(self):
        eng = self._engine
        if eng is None:
            return DEFAULT_BPM
        return eng.bpm if eng.bpm else DEFAULT_BPM

    def _run(self, initial_root):
        last_note = None
        last_root = initial_root
        eng = self._engine
        ch = ARP_CHANNEL

        while True:
            with self._lock:
                if not self._active:
                    break
                current_mode = self._mode

            intervals = MAJOR_INTERVALS if current_mode == "major" else MINOR_INTERVALS
            root = self._current_root()

            if root != last_root:
                if last_note is not None:
                    try:
                        eng.note_off(key=last_note, channel=ch)
                    except Exception:
                        pass
                    last_note = None
                last_root = root

            if root is None:
                time.sleep(0.04)
                continue

            interval = 60.0 / self._bpm() / 4  # semicorcheas
            chord = [root + i for i in intervals]
            choices = [n for n in chord if n != last_note] or chord
            note = max(0, min(127, random.choice(choices)))

            if last_note is not None and last_note != note:
                try:
                    eng.note_off(key=last_note, channel=ch)
                except Exception:
                    pass

            try:
                eng.note_on(key=note, velocity=90, channel=ch)
                last_note = note
            except Exception:
                break

            time.sleep(interval)

        if last_note is not None:
            try:
                eng.note_off(key=last_note, channel=ch)
            except Exception:
                pass


arpeggiator = Arpeggiator()
