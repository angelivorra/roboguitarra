"""Arpegiador de acorde mayor sincronizado con el BPM de la canción.

Botón 2 mantenido → arp ON (CC 22 = 127).
Botón 2 suelto    → arp OFF (CC 22 = 0).

Mientras está activo el arp sigue automáticamente la nota más grave
que esté sonando en ese momento (excluyendo su propio canal).
Cuando la nota cambia, el arpegio reinicia desde la raíz.
Si no hay nota, espera en silencio.
"""
import threading
import time

MAJOR = [0, 4, 7, 12]   # root, 3ª mayor, 5ª justa, octava
ARP_CHANNEL = 3          # canal FluidSynth reservado (ya programado con el mismo instrumento)
DEFAULT_BPM = 180.0      # tempo si no hay señal TCP


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

    def start(self):
        """Arranca el arp (botón pulsado). Sin efecto si ya está activo."""
        with self._lock:
            if self._active:
                return
            self._active = True
        threading.Thread(target=self._run, daemon=True, name="arp").start()

    def stop(self):
        """Para el arp (botón suelto)."""
        with self._lock:
            self._active = False

    # ----------------------------------------------------------------- interno

    def _current_root(self):
        """Nota más grave sonando ahora, ignorando el canal del arp."""
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

    def _run(self):
        step = 0
        last_note = None
        last_root = None
        eng = self._engine
        ch = ARP_CHANNEL

        while True:
            with self._lock:
                if not self._active:
                    break

            root = self._current_root()

            # Nueva nota raíz: reinicia el arpegio desde el principio
            if root != last_root:
                if last_note is not None:
                    try:
                        eng.note_off(key=last_note, channel=ch)
                    except Exception:
                        pass
                    last_note = None
                step = 0
                last_root = root

            if root is None:
                time.sleep(0.04)
                continue

            chord = [root + i for i in MAJOR]
            interval = 60.0 / self._bpm() / 2  # corcheas

            note = chord[step % len(chord)]

            if last_note is not None and last_note != note:
                try:
                    eng.note_off(key=last_note, channel=ch)
                except Exception:
                    pass

            try:
                eng.note_on(key=note, velocity=80, channel=ch)
                last_note = note
            except Exception:
                break

            step += 1
            time.sleep(interval)

        # Limpieza al soltar el botón
        if last_note is not None:
            try:
                eng.note_off(key=last_note, channel=ch)
            except Exception:
                pass


arpeggiator = Arpeggiator()
