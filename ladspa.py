"""Bindings ctypes para el motor LADSPA integrado de FluidSynth.

pyfluidsynth no envuelve la API `fluid_ladspa_*`, pero libfluidsynth la exporta,
así que la llamamos directamente sobre el handle nativo que pyfluidsynth ya
cargó (`fluidsynth._fl`).

Insertamos el "Bode frequency shifter" (swh-plugins) en la salida principal y
exponemos su control "Frequency shift" en tiempo real para mapearlo a un knob.
Da un timbre metálico/robótico (0 Hz = sin efecto).
"""
import ctypes as C
import os

import fluidsynth as _fs

_lib = _fs._fl  # CDLL de libfluidsynth ya cargada por pyfluidsynth

FLUID_OK = 0

# Plugin (ruta configurable por si en la Pi está en otra ubicación)
BODE_LIB = os.environ.get(
    "ROBOGUITARRA_BODE_LIB", "/usr/lib/ladspa/bode_shifter_1431.so"
)
LABEL = b"bodeShifter"
CTRL = b"Frequency shift"   # control 0..5000 Hz
PORT_IN = b"Input"
# Conectamos las DOS salidas a la principal: FluidSynth las suma (banda
# superior + inferior), lo que da un timbre más robótico tipo ring-mod.
PORTS_OUT = (b"Up out", b"Down out")
MAX_HZ = 5000.0


def _decl(name, restype, *argtypes):
    fn = getattr(_lib, name)
    fn.restype = restype
    fn.argtypes = list(argtypes)
    return fn


_get_fx = _decl("fluid_synth_get_ladspa_fx", C.c_void_p, C.c_void_p)
_add_effect = _decl("fluid_ladspa_add_effect", C.c_int, C.c_void_p, C.c_char_p, C.c_char_p, C.c_char_p)
_add_buffer = _decl("fluid_ladspa_add_buffer", C.c_int, C.c_void_p, C.c_char_p)
_link = _decl("fluid_ladspa_effect_link", C.c_int, C.c_void_p, C.c_char_p, C.c_char_p, C.c_char_p)
_set_control = _decl("fluid_ladspa_effect_set_control", C.c_int, C.c_void_p, C.c_char_p, C.c_char_p, C.c_float)
_activate = _decl("fluid_ladspa_activate", C.c_int, C.c_void_p)
_reset = _decl("fluid_ladspa_reset", C.c_int, C.c_void_p)
_is_active = _decl("fluid_ladspa_is_active", C.c_int, C.c_void_p)
_check = _decl("fluid_ladspa_check", C.c_int, C.c_void_p, C.c_char_p, C.c_int)


class FreqShifter:
    """Inserta el Bode frequency shifter en la salida principal (L y R)."""

    def __init__(self, synth_ptr):
        self.fx = _get_fx(synth_ptr)
        self.loaded = False
        self.error = None
        self.value = 0.0

    def load(self):
        if not self.fx:
            self.error = "LADSPA no disponible en FluidSynth"
            return False
        if not os.path.exists(BODE_LIB):
            self.error = f"No se encontró el plugin: {BODE_LIB} (instala swh-plugins)"
            return False

        if _is_active(self.fx):
            _reset(self.fx)

        # Una instancia mono por canal, insertada sobre la salida principal.
        lib = BODE_LIB.encode()
        for name, main in ((b"shiftL", b"Main:L"), (b"shiftR", b"Main:R")):
            if _add_effect(self.fx, name, lib, LABEL) != FLUID_OK:
                self.error = f"add_effect falló ({name.decode()})"
                return False
            _link(self.fx, name, PORT_IN, main)        # lee de Main:x
            for out in PORTS_OUT:
                _link(self.fx, name, out, main)        # ambas salidas -> Main:x

        err = C.create_string_buffer(512)
        if _check(self.fx, err, 512) != FLUID_OK:
            self.error = err.value.decode("utf-8", "replace") or "ladspa_check falló"
            _reset(self.fx)
            return False

        _activate(self.fx)
        self.loaded = True
        self.set(0.0)
        return True

    def set(self, hz):
        if not self.loaded:
            return
        self.value = max(0.0, min(MAX_HZ, float(hz)))
        for name in (b"shiftL", b"shiftR"):
            _set_control(self.fx, name, CTRL, C.c_float(self.value))
