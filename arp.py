"""Arpegiador de acorde mayor sincronizado con el BPM de la canción.

El botón 2 (CC 22) activa/desactiva el arpegio. La nota raíz se captura
en el momento de la pulsación: primero se mira la nota más grave sonando;
si no hay ninguna, se usa el traste actual de la cuerda más grave activa.
El arpegio corre en un hilo propio a corcheas (60/BPM/2 segundos).
"""
import threading
import time

# Intervalos del acorde mayor (semitonos sobre la raíz)
MAJOR = [0, 4, 7, 12]
ARP_CHANNEL = 3      # canal FluidSynth reservado para el arpegiador
DEFAULT_BPM = 180.0  # tempo si no se ha recibido BPM por TCP


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
        """Arranca o para el arpegiador. Devuelve True si acaba de arrancar."""
        with self._lock:
            if self._active:
                self._active = False
                return False
            root = self._root_now()
            if root is None:
                return False
            self._active = True

        threading.Thread(
            target=self._run, args=(root,), daemon=True, name="arp"
        ).start()
        return True

    def stop(self):
        with self._lock:
            self._active = False

    # ----------------------------------------------------------------- interno

    def _root_now(self):
        """Nota raíz: la más grave sonando ahora, o la del traste actual."""
        eng = self._engine
        if eng is None:
            return None

        sounding = eng.get_state().get("sounding") or []
        notes = [s["note"] for s in sounding if s.get("note") is not None]
        if notes:
            return min(notes)

        # No hay nota sonando: busca el traste actual en el mastil
        try:
            import mastil
            from mastil import OPEN_NOTES

            snap = mastil.monitor.snapshot()
            candidates = []
            for i, s in enumerate(snap["strings"]):
                if s.get("finger") and s.get("fret") is not None:
                    candidates.append(OPEN_NOTES[i] + int(s["fret"]) + 1)
                elif s.get("fret") is None and not s.get("finger"):
                    # cuerda al aire
                    candidates.append(OPEN_NOTES[i])
            if candidates:
                return min(candidates)
        except Exception:
            pass

        return None

    def _bpm(self):
        eng = self._engine
        if eng is None:
            return DEFAULT_BPM
        return eng.bpm if eng.bpm else DEFAULT_BPM

    def _run(self, root):
        chord = [root + i for i in MAJOR]
        step = 0
        last_note = None
        eng = self._engine
        ch = ARP_CHANNEL

        while True:
            with self._lock:
                if not self._active:
                    break

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

        # Limpieza final
        if last_note is not None:
            try:
                eng.note_off(key=last_note, channel=ch)
            except Exception:
                pass


arpeggiator = Arpeggiator()
