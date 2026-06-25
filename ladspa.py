"""Bindings ctypes para el motor LADSPA integrado de FluidSynth.

pyfluidsynth no envuelve la API `fluid_ladspa_*`, pero libfluidsynth la exporta,
así que la llamamos sobre el handle nativo que pyfluidsynth ya cargó
(`fluidsynth._fl`).

Montamos una cadena robótica en la salida principal (por canal L y R):

    Main -> [Bode frequency shifter] -> [Decimator] -> Main

El knob "Robot" es bipolar (centro = limpio):
  * hacia arriba  -> frequency shifter (timbre metálico, swh bodeShifter)
  * hacia abajo   -> decimator / bitcrusher (robot de 8 bits, swh decimator)
En el centro ambos quedan transparentes (shift 0 Hz, 24 bits, sample rate full).
"""
import ctypes as C
import os

import fluidsynth as _fs

_lib = _fs._fl  # CDLL de libfluidsynth ya cargada por pyfluidsynth

FLUID_OK = 0

# Rutas de los plugins (configurables por si en la Pi están en otra ubicación)
BODE_LIB = os.environ.get(
    "ROBOGUITARRA_BODE_LIB", "/usr/lib/ladspa/bode_shifter_1431.so"
)
DECIM_LIB = os.environ.get(
    "ROBOGUITARRA_DECIM_LIB", "/usr/lib/ladspa/decimator_1202.so"
)

# Extremos del bitcrusher (24 bits / sample rate full = transparente)
MIN_BITS = 4.0
MIN_SR = 3000.0
SAMPLE_RATE = 44100.0


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
    """Cadena shifter -> decimator insertada en la salida principal (L y R)."""

    def __init__(self, synth_ptr):
        self.fx = _get_fx(synth_ptr)
        self.loaded = False
        self.error = None
        self._shifters = (b"shiftL", b"shiftR")
        self._decimators = (b"decimL", b"decimR")

    def load(self):
        if not self.fx:
            self.error = "LADSPA no disponible en FluidSynth"
            return False
        for lib in (BODE_LIB, DECIM_LIB):
            if not os.path.exists(lib):
                self.error = f"Falta el plugin {lib} (instala swh-plugins)"
                return False

        if _is_active(self.fx):
            _reset(self.fx)

        bode = BODE_LIB.encode()
        decim = DECIM_LIB.encode()
        # Por canal: shifter primero, decimator después (orden de adición = orden
        # de proceso). El shifter tiene 2 salidas; ambas van a Main (se suman).
        for sh, dec, main in (
            (b"shiftL", b"decimL", b"Main:L"),
            (b"shiftR", b"decimR", b"Main:R"),
        ):
            if _add_effect(self.fx, sh, bode, b"bodeShifter") != FLUID_OK:
                self.error = f"add_effect bodeShifter falló ({sh.decode()})"
                return False
            _link(self.fx, sh, b"Input", main)
            _link(self.fx, sh, b"Up out", main)
            _link(self.fx, sh, b"Down out", main)
            if _add_effect(self.fx, dec, decim, b"decimator") != FLUID_OK:
                self.error = f"add_effect decimator falló ({dec.decode()})"
                return False
            _link(self.fx, dec, b"Input", main)
            _link(self.fx, dec, b"Output", main)

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
        """t en [-1, 1]: 0 = limpio, >0 = frequency shifter, <0 = bitcrusher."""
        if not self.loaded:
            return
        t = max(-1.0, min(1.0, float(t)))
        up = max(0.0, t)
        down = max(0.0, -t)
        shift_hz = up * 1500.0
        bits = 24.0 + down * (MIN_BITS - 24.0)
        srate = SAMPLE_RATE - down * (SAMPLE_RATE - MIN_SR)
        for name in self._shifters:
            _set_control(self.fx, name, b"Frequency shift", C.c_float(shift_hz))
        for name in self._decimators:
            _set_control(self.fx, name, b"Bit depth", C.c_float(bits))
            _set_control(self.fx, name, b"Sample rate (Hz)", C.c_float(srate))
