"""Arpegiador de acorde mayor sincronizado con el BPM de la canción.

Botón 2 → toggle: primer toque activa, segundo toque desactiva.

Mientras está activo sigue automáticamente la nota más grave que esté
sonando (excluyendo su propio canal). Cuando la nota cambia, el arpegio
reinicia desde el principio.
"""
import threading
import time

# Dos octavas arriba y de vuelta: sube hasta el 24 y baja zigzagueando
PATTERN = [0, 4, 7, 12, 7, 4, 0, 12, 19, 24, 19, 12, 7, 4, 0, 4]

ARP_CHANNEL = 3
DEFAULT_BPM = 180.0


class Arpeggiator:
    def __init__(self):
        self._lock = threading.Lock()
        self._active = False
        self._engine = None

    def bind(self, engine):
        self._engine = engine

    @property
    def active(self):
        with self._lock:
            return self._active

    def toggle(self):
        """Alterna entre activo e inactivo."""
        root = self._current_root()  # fuera del lock para evitar deadlock
        with self._lock:
            if self._active:
                self._active = False
                return
            self._active = True
        threading.Thread(target=self._run, args=(root,), daemon=True, name="arp").start()

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
        step = 0
        last_note = None
        last_root = initial_root      # raíz actualmente seguida
        latched_root = initial_root   # última raíz vista (se mantiene al soltar)
        eng = self._engine
        ch = ARP_CHANNEL

        while True:
            with self._lock:
                if not self._active:
                    break

            root = self._current_root()

            # Actualiza la nota latched cuando hay una nota sonando
            if root is not None and root != latched_root:
                latched_root = root

            # Reinicia el paso solo cuando cambia la nota real (no en silencio)
            if root != last_root:
                if root is not None:
                    if last_note is not None:
                        try:
                            eng.note_off(key=last_note, channel=ch)
                        except Exception:
                            pass
                        last_note = None
                    step = 0
                last_root = root

            # Usa la nota latcheada para seguir tocando aunque sueltes la cuerda
            effective_root = latched_root
            if effective_root is None:
                time.sleep(0.04)
                continue

            # semicorcheas: 60/BPM/4 por paso
            interval = 60.0 / self._bpm() / 4

            note = effective_root + PATTERN[step % len(PATTERN)]
            note = max(0, min(127, note))

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

            step += 1
            time.sleep(interval)

        if last_note is not None:
            try:
                eng.note_off(key=last_note, channel=ch)
            except Exception:
                pass


arpeggiator = Arpeggiator()
