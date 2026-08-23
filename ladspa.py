"""Bindings ctypes para el motor LADSPA integrado de FluidSynth.

pyfluidsynth no envuelve la API `fluid_ladspa_*`, pero libfluidsynth la exporta,
así que la llamamos sobre el handle nativo que pyfluidsynth ya cargó
(`fluidsynth._fl`).

Montamos una cadena robótica en la salida principal (por canal L y R):

    Main -> [LFO Phaser] -> [Decimator arriba] -> [Decimator abajo] -> Main

El knob "Robot" es bipolar (centro = limpio):
  * hacia arriba  -> phaser agresivo (swh lfoPhaser) + bits muy reducidos
    (SIN bajar el sample rate). Medido a fondo: solo el phaser (o solo el
    decimator con bits moderados, o solo bajar el sample rate) apenas
    cambiaba el RMS de la nota — nada de eso se notaba en esta cadena real
    (con reverb/chorus de producción). Solo funciona de forma fiable
    reduciendo los BITS de forma extrema (~2, casi binario): ahí la subida
    de RMS es real y grande. Combinando eso con el phaser se consigue un
    barrido metálico con mordiente, distinto del lado de abajo (que además
    baja el sample rate, más apagado/enmudecido).
  * hacia abajo   -> decimator / bitcrusher (robot de 8 bits, swh decimator),
    bits Y sample rate reducidos. El suelo real del plugin es 1 bit / ~44 Hz,
    pero ahí es degenerado: por debajo del oído humano (<20 Hz) y con 1 bit
    se puede colapsar a silencio o una cuadrada fija según el nivel de
    entrada. Se deja un suelo audible.
Antes se probaron (y descartaron por no notarse en esta cadena real, con
reverb/chorus, aunque en pruebas aisladas parecían funcionar): ring
modulator, foldover, wave shaper, sinus wavewrapper, phaser solo, GSM
(con errores aleatorios clipea de forma impredecible; sin errores no hace
nada) y aliasing.
En el centro todo queda transparente (feedback 0, 24 bits, sample rate
full).
"""
import ctypes as C
import os

import fluidsynth as _fs

_lib = _fs._fl  # CDLL de libfluidsynth ya cargada por pyfluidsynth

FLUID_OK = 0

# Rutas de los plugins (configurables por si en la Pi están en otra ubicación)
PHASER_LIB = os.environ.get(
    "ROBOGUITARRA_PHASER_LIB", "/usr/lib/ladspa/phasers_1217.so"
)
DECIM_LIB = os.environ.get(
    "ROBOGUITARRA_DECIM_LIB", "/usr/lib/ladspa/decimator_1202.so"
)

MAX_RATE = 18.0
MAX_FEEDBACK = 0.75
MAX_SPREAD = 1.6

MAX_BITS = 24.0
SAMPLE_RATE = 44100.0

MIN_BITS_UP = 4.0
MIN_BITS_DOWN = 4.0
MIN_SR_DOWN = 4000.0


def _decl(name, restype, *argtypes):
    fn = getattr(_lib, name)
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


_get_fx = _decl("fluid_synth_get_ladspa_fx", C.c_void_p, C.c_void_p)
_add_effect = _decl("fluid_ladspa_add_effect", C.c_int, C.c_void_p, C.c_char_p, C.c_char_p, C.c_char_p)
_link = _decl("fluid_ladspa_effect_link", C.c_int, C.c_void_p, C.c_char_p, C.c_char_p, C.c_char_p)
_set_control = _decl("fluid_ladspa_effect_set_control", C.c_int, C.c_void_p, C.c_char_p, C.c_char_p, C.c_float)
_activate = _decl("fluid_ladspa_activate", C.c_int, C.c_void_p)
_reset = _decl("fluid_ladspa_reset", C.c_int, C.c_void_p)
_is_active = _decl("fluid_ladspa_is_active", C.c_int, C.c_void_p)
_check = _decl("fluid_ladspa_check", C.c_int, C.c_void_p, C.c_char_p, C.c_int)


class RobotFx:
    """Cadena phaser+crush (arriba) y decimator (abajo), L y R."""

    def __init__(self, synth_ptr):
        self.fx = _get_fx(synth_ptr)
        self.loaded = False
        self.error = None
        self._phasers = (b"phaseL", b"phaseR")
        self._crush_up = (b"crushUpL", b"crushUpR")
        self._decimators = (b"decimL", b"decimR")

    def load(self):
        if not self.fx:
            self.error = "LADSPA no disponible en FluidSynth"
            return False
        for lib in (PHASER_LIB, DECIM_LIB):
            if not os.path.exists(lib):
                self.error = f"Falta el plugin {lib} (instala swh-plugins)"
                return False

        if _is_active(self.fx):
            _reset(self.fx)

        phaser = PHASER_LIB.encode()
        decim = DECIM_LIB.encode()
        # Por canal, en orden de proceso: phaser -> crush de arriba ->
        # decimator de abajo. Todos leen/escriben en el mismo bus Main.
        for ph, cu, dn, main in (
            (b"phaseL", b"crushUpL", b"decimL", b"Main:L"),
            (b"phaseR", b"crushUpR", b"decimR", b"Main:R"),
        ):
            if _add_effect(self.fx, ph, phaser, b"lfoPhaser") != FLUID_OK:
                self.error = f"add_effect lfoPhaser falló ({ph.decode()})"
                return False
            _link(self.fx, ph, b"Input", main)
            _link(self.fx, ph, b"Output", main)

            if _add_effect(self.fx, cu, decim, b"decimator") != FLUID_OK:
                self.error = f"add_effect decimator (crush arriba) falló ({cu.decode()})"
                return False
            _link(self.fx, cu, b"Input", main)
            _link(self.fx, cu, b"Output", main)

            if _add_effect(self.fx, dn, decim, b"decimator") != FLUID_OK:
                self.error = f"add_effect decimator (abajo) falló ({dn.decode()})"
                return False
            _link(self.fx, dn, b"Input", main)
            _link(self.fx, dn, b"Output", main)

        err = C.create_string_buffer(512)
        if _check(self.fx, err, 512) != FLUID_OK:
            self.error = err.value.decode("utf-8", "replace") or "ladspa_check falló"
            _reset(self.fx)
            return False

        _activate(self.fx)
        self.loaded = True
        self.set_robot(0.0)  # arranca limpio
        return True

    def set_robot(self, t):
        """t en [-1, 1]: 0 = limpio, >0 = phaser+crush, <0 = bitcrusher."""
        if not self.loaded:
            return
        t = max(-1.0, min(1.0, float(t)))
        up = max(0.0, t)
        down = max(0.0, -t)

        rate = up * MAX_RATE
        feedback = up * MAX_FEEDBACK
        spread = 1.0 + up * (MAX_SPREAD - 1.0)
        # sqrt(up) en vez de up: la reducción de bits se nota ya desde el
        # principio del recorrido, no solo en la última fracción del stick.
        bits_up = MAX_BITS * (MIN_BITS_UP / MAX_BITS) ** (up ** 0.5)

        down_curva = down ** 0.5
        bits_down = MAX_BITS * (MIN_BITS_DOWN / MAX_BITS) ** down_curva
        srate_down = SAMPLE_RATE * (MIN_SR_DOWN / SAMPLE_RATE) ** down_curva

        for name in self._phasers:
            _set_control(self.fx, name, b"LFO rate (Hz)", C.c_float(rate))
            _set_control(self.fx, name, b"LFO depth", C.c_float(up))
            _set_control(self.fx, name, b"Feedback", C.c_float(feedback))
            _set_control(self.fx, name, b"Spread (octaves)", C.c_float(spread))
        for name in self._crush_up:
            _set_control(self.fx, name, b"Bit depth", C.c_float(bits_up))
            _set_control(self.fx, name, b"Sample rate (Hz)", C.c_float(SAMPLE_RATE))
        for name in self._decimators:
            _set_control(self.fx, name, b"Bit depth", C.c_float(bits_down))
            _set_control(self.fx, name, b"Sample rate (Hz)", C.c_float(srate_down))
