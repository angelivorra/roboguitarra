"""Bindings ctypes para el motor LADSPA integrado de FluidSynth.

La cadena no se monta hasta que el knob Robot deja el centro. En limpio
`fluid_ladspa_reset` deja el grafo vacío: la salida es la de FluidSynth,
sin plugins. Al elegir un efecto se instancian solo sus plugins (L y R).
Al cambiar de preset (o de lado del knob) se tira el grafo y se arma el nuevo.
"""
import ctypes as C
import os

import fluidsynth as _fs

_lib = _fs._fl  # CDLL de libfluidsynth ya cargada por pyfluidsynth

FLUID_OK = 0

_LADSPA_DIR = "/usr/lib/ladspa"
_MAINS = (b"Main:L", b"Main:R")

PHASER_LIB = os.environ.get("ROBOGUITARRA_PHASER_LIB", f"{_LADSPA_DIR}/phasers_1217.so")
DECIM_LIB = os.environ.get("ROBOGUITARRA_DECIM_LIB", f"{_LADSPA_DIR}/decimator_1202.so")
FLANGE_LIB = os.environ.get("ROBOGUITARRA_FLANGE_LIB", f"{_LADSPA_DIR}/flanger_1191.so")
WAH_LIB = os.environ.get("ROBOGUITARRA_WAH_LIB", f"{_LADSPA_DIR}/svf_1214.so")
RING_LIB = os.environ.get("ROBOGUITARRA_RING_LIB", f"{_LADSPA_DIR}/ringmod_1188.so")
DIST_LIB = os.environ.get("ROBOGUITARRA_DIST_LIB", f"{_LADSPA_DIR}/chebstortion_1430.so")
DJFLANGE_LIB = os.environ.get("ROBOGUITARRA_DJFLANGE_LIB", f"{_LADSPA_DIR}/dj_flanger_1438.so")
VALVE_LIB = os.environ.get("ROBOGUITARRA_VALVE_LIB", f"{_LADSPA_DIR}/valve_1209.so")
XOVER_LIB = os.environ.get("ROBOGUITARRA_XOVER_LIB", f"{_LADSPA_DIR}/crossover_dist_1404.so")
WRAP_LIB = os.environ.get("ROBOGUITARRA_WRAP_LIB", f"{_LADSPA_DIR}/sinus_wavewrapper_1198.so")
SIFTER_LIB = os.environ.get("ROBOGUITARRA_SIFTER_LIB", f"{_LADSPA_DIR}/sifter_1210.so")
COMB_LIB = os.environ.get("ROBOGUITARRA_COMB_LIB", f"{_LADSPA_DIR}/comb_1190.so")
SMOOTH_LIB = os.environ.get("ROBOGUITARRA_SMOOTH_LIB", f"{_LADSPA_DIR}/smooth_decimate_1414.so")
RATE_LIB = os.environ.get("ROBOGUITARRA_RATE_LIB", f"{_LADSPA_DIR}/rate_shifter_1417.so")
HERMES_LIB = os.environ.get("ROBOGUITARRA_HERMES_LIB", f"{_LADSPA_DIR}/hermes_filter_1200.so")
TAPE_LIB = os.environ.get("ROBOGUITARRA_TAPE_LIB", f"{_LADSPA_DIR}/tape_delay_1211.so")
AMP_LIB = os.environ.get("ROBOGUITARRA_AMP_LIB", f"{_LADSPA_DIR}/amp_1181.so")

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


def _ctl(fx, names, port, value):
    for name in names:
        _set_control(fx, name, port, C.c_float(value))


class RobotFx:
    """Grafo LADSPA mínimo: vacío en limpio, un efecto cuando el knob se mueve."""

    def __init__(self, synth_ptr):
        self.fx = _get_fx(synth_ptr)
        self.loaded = False
        self.error = None
        self._active_id = None
        self._phasers = (b"phaseL", b"phaseR")
        self._crush_up = (b"crushUpL", b"crushUpR")
        self._decimators = (b"decimL", b"decimR")
        self._flangers = (b"flangeL", b"flangeR")
        self._wahs = (b"wahL", b"wahR")
        self._rings = (b"ringL", b"ringR")
        self._dists = (b"distL", b"distR")
        self._djflanges = (b"djflL", b"djflR")
        self._valves = (b"valveL", b"valveR")
        self._xovers = (b"xoverL", b"xoverR")
        self._wraps = (b"wrapL", b"wrapR")
        self._wrap_amps = (b"wrapAmpL", b"wrapAmpR")
        self._sifters = (b"siftL", b"siftR")
        self._combs = (b"combL", b"combR")
        self._smooths = (b"smoothL", b"smoothR")
        self._rates = (b"rateL", b"rateR")
        self._hermes = (b"hermesL", b"hermesR")
        self._delays = (b"delayL", b"delayR")
        self._delay_kind = None
        self._libs = {
            "phaser": PHASER_LIB,
            "bitcrush": DECIM_LIB,
            "flanger": FLANGE_LIB,
            "wah": WAH_LIB,
            "ringmod": RING_LIB,
            "distort": DIST_LIB,
            "djflanger": DJFLANGE_LIB,
            "valve": VALVE_LIB,
            "crossover": XOVER_LIB,
            "wavewrap": WRAP_LIB,
            "amp": AMP_LIB,
            "sifter": SIFTER_LIB,
            "comb": COMB_LIB,
            "smoothdecim": SMOOTH_LIB,
            "rateshift": RATE_LIB,
            "hermes": HERMES_LIB,
            "delay": TAPE_LIB,
        }

    def load(self):
        """Comprueba que LADSPA y los .so existen. No monta ningún plugin."""
        if not self.fx:
            self.error = "LADSPA no disponible en FluidSynth"
            return False
        for lib in self._libs.values():
            if not os.path.exists(lib):
                self.error = f"Falta el plugin {lib} (instala swh-plugins)"
                return False
        self._tear_down()
        self.loaded = True
        return True

    def _tear_down(self):
        """Grafo vacío: audio limpio, sin plugins en la cadena."""
        if self.fx:
            _reset(self.fx)
        self._active_id = None
        self._delay_kind = None

    def _add_stereo(self, names, lib, label):
        lib_b = lib.encode()
        for name, main in zip(names, _MAINS):
            if _add_effect(self.fx, name, lib_b, label) != FLUID_OK:
                self.error = f"add_effect {label.decode()} falló ({name.decode()})"
                return False
            _link(self.fx, name, b"Input", main)
            _link(self.fx, name, b"Output", main)
        return True

    def _add_robot(self, effect_id):
        if effect_id == "phaser":
            return self._add_stereo(self._phasers, PHASER_LIB, b"lfoPhaser") and self._add_stereo(
                self._crush_up, DECIM_LIB, b"decimator"
            )
        if effect_id == "bitcrush":
            return self._add_stereo(self._decimators, DECIM_LIB, b"decimator")
        if effect_id == "flanger":
            return self._add_stereo(self._flangers, FLANGE_LIB, b"flanger")
        if effect_id == "wah":
            return self._add_stereo(self._wahs, WAH_LIB, b"svf")
        if effect_id == "ringmod":
            return self._add_stereo(self._rings, RING_LIB, b"ringmod_1i1o1l")
        if effect_id == "distort":
            return self._add_stereo(self._dists, DIST_LIB, b"chebstortion") and self._add_stereo(
                self._crush_up, DECIM_LIB, b"decimator"
            )
        if effect_id == "djflanger":
            return self._add_stereo(self._djflanges, DJFLANGE_LIB, b"djFlanger")
        if effect_id == "valve":
            return self._add_stereo(self._valves, VALVE_LIB, b"valve")
        if effect_id == "crossover":
            return self._add_stereo(self._xovers, XOVER_LIB, b"crossoverDist")
        if effect_id == "wavewrap":
            return self._add_stereo(self._wraps, WRAP_LIB, b"sinusWavewrapper") and self._add_stereo(
                self._wrap_amps, AMP_LIB, b"amp"
            )
        if effect_id == "sifter":
            return self._add_stereo(self._sifters, SIFTER_LIB, b"sifter")
        if effect_id == "comb":
            return self._add_stereo(self._combs, COMB_LIB, b"comb")
        if effect_id == "smoothdecim":
            return self._add_stereo(self._smooths, SMOOTH_LIB, b"smoothDecimate")
        if effect_id == "rateshift":
            return self._add_stereo(self._rates, RATE_LIB, b"rateShifter")
        if effect_id == "hermes":
            return self._add_stereo(self._hermes, HERMES_LIB, b"hermesFilter")
        self.error = f"Efecto desconocido: {effect_id}"
        return False

    def _build(self, effect_id, delay_kind):
        self._tear_down()
        if effect_id and not self._add_robot(effect_id):
            self._tear_down()
            return False
        if delay_kind in ("delay", "tape"):
            if not self._add_stereo(self._delays, TAPE_LIB, b"tapeDelay"):
                self._tear_down()
                return False
        err = C.create_string_buffer(512)
        if _check(self.fx, err, 512) != FLUID_OK:
            self.error = err.value.decode("utf-8", "replace") or "ladspa_check falló"
            self._tear_down()
            return False
        if _activate(self.fx) != FLUID_OK:
            self.error = "ladspa_activate falló"
            self._tear_down()
            return False
        self._active_id = effect_id
        self._delay_kind = delay_kind if delay_kind in ("delay", "tape") else None
        return True

    def _apply_phaser(self, amount):
        rate = amount * MAX_RATE
        feedback = amount * MAX_FEEDBACK
        spread = 1.0 + amount * (MAX_SPREAD - 1.0)
        bits_up = MAX_BITS * (MIN_BITS_UP / MAX_BITS) ** (amount ** 0.5)
        _ctl(self.fx, self._phasers, b"LFO rate (Hz)", rate)
        _ctl(self.fx, self._phasers, b"LFO depth", amount)
        _ctl(self.fx, self._phasers, b"Feedback", feedback)
        _ctl(self.fx, self._phasers, b"Spread (octaves)", spread)
        _ctl(self.fx, self._crush_up, b"Bit depth", bits_up)
        _ctl(self.fx, self._crush_up, b"Sample rate (Hz)", SAMPLE_RATE)

    def _apply_bitcrush(self, amount):
        curva = amount ** 0.5
        bits = MAX_BITS * (MIN_BITS_DOWN / MAX_BITS) ** curva
        srate = SAMPLE_RATE * (MIN_SR_DOWN / SAMPLE_RATE) ** curva
        _ctl(self.fx, self._decimators, b"Bit depth", bits)
        _ctl(self.fx, self._decimators, b"Sample rate (Hz)", srate)

    def _apply_flanger(self, amount):
        _ctl(self.fx, self._flangers, b"Delay base (ms)", 1.5 + amount * 8.0)
        _ctl(self.fx, self._flangers, b"Max slowdown (ms)", 0.8 + amount * 5.0)
        _ctl(self.fx, self._flangers, b"LFO frequency (Hz)", 0.2 + amount * 4.5)
        _ctl(self.fx, self._flangers, b"Feedback", amount * 0.65)

    def _apply_wah(self, amount):
        freq = 4500.0 * (1100.0 / 4500.0) ** amount
        _ctl(self.fx, self._wahs, b"Filter type (0=none, 1=LP, 2=HP, 3=BP, 4=BR, 5=AP)", 1.0)
        _ctl(self.fx, self._wahs, b"Filter freq", freq)
        _ctl(self.fx, self._wahs, b"Filter Q", 0.18 + amount * 0.22)
        _ctl(self.fx, self._wahs, b"Filter resonance", amount * 0.20)

    def _apply_ringmod(self, amount):
        _ctl(self.fx, self._rings, b"Modulation depth (0=none, 1=AM, 2=RM)", 2.0 * (amount ** 0.5))
        _ctl(self.fx, self._rings, b"Frequency (Hz)", 40.0 + amount * 220.0)
        _ctl(self.fx, self._rings, b"Sine level", 1.0)
        _ctl(self.fx, self._rings, b"Triangle level", 0.0)
        _ctl(self.fx, self._rings, b"Sawtooth level", 0.0)
        _ctl(self.fx, self._rings, b"Square level", 0.0)

    def _apply_distort(self, amount):
        curva = amount ** 0.5
        bits = MAX_BITS * (MIN_BITS_DOWN / MAX_BITS) ** curva
        _ctl(self.fx, self._dists, b"Distortion", amount * 3.0)
        _ctl(self.fx, self._crush_up, b"Bit depth", bits)
        _ctl(self.fx, self._crush_up, b"Sample rate (Hz)", SAMPLE_RATE)

    def _apply_djflanger(self, amount):
        _ctl(self.fx, self._djflanges, b"LFO period (s)", 2.4 - amount * 2.2)
        _ctl(self.fx, self._djflanges, b"LFO depth (ms)", 1.2 + amount * 3.6)
        _ctl(self.fx, self._djflanges, b"Feedback (%)", amount * 72.0)

    def _apply_valve(self, amount):
        # El valve de SWH es suave: curva rápida + suelo para que se oiga
        # ya a mitad de knob, y character a tope al final.
        hot = amount ** 0.4
        _ctl(self.fx, self._valves, b"Distortion level", min(1.0, 0.28 + hot * 0.72))
        _ctl(self.fx, self._valves, b"Distortion character", min(1.0, 0.5 + amount * 0.5))

    def _apply_crossover(self, amount):
        _ctl(self.fx, self._xovers, b"Crossover amplitude", amount * 0.085)
        _ctl(self.fx, self._xovers, b"Smoothing", 0.75 - amount * 0.45)

    def _apply_wavewrap(self, amount):
        # El plegado llena la onda y dispara el volumen; el amp lo tira abajo.
        _ctl(self.fx, self._wraps, b"Wrap degree", amount * 4.2)
        _ctl(self.fx, self._wrap_amps, b"Amps gain (dB)", -amount * 12.0)

    def _apply_sifter(self, amount):
        _ctl(self.fx, self._sifters, b"Sift size", 1.0 + amount * 220.0)

    def _apply_comb(self, amount):
        _ctl(self.fx, self._combs, b"Band separation (Hz)", 70.0 + amount * 420.0)
        _ctl(self.fx, self._combs, b"Feedback", amount * 0.82)

    def _apply_smoothdecim(self, amount):
        curva = amount ** 0.5
        rate = SAMPLE_RATE * (800.0 / SAMPLE_RATE) ** curva
        _ctl(self.fx, self._smooths, b"Resample rate", rate)
        _ctl(self.fx, self._smooths, b"Smoothing", 0.85 - amount * 0.55)

    def _apply_rateshift(self, amount):
        _ctl(self.fx, self._rates, b"Rate", 1.0 - amount * 0.72)

    def _apply_hermes(self, amount):
        freq = 3800.0 * (380.0 / 3800.0) ** amount
        _ctl(self.fx, self._hermes, b"LFO1 freq (Hz)", 0.15 + amount * 7.0)
        _ctl(self.fx, self._hermes, b"LFO1 wave (0 = sin, 1 = tri, 2 = saw, 3 = squ, 4 = s&h)", 0.0)
        _ctl(self.fx, self._hermes, b"Input gain (dB)", 0.0)
        _ctl(self.fx, self._hermes, b"Filt1 type (0=none, 1=LP, 2=HP, 3=BP, 4=BR, 5=AP)", 1.0)
        _ctl(self.fx, self._hermes, b"Filt1 freq", freq)
        _ctl(self.fx, self._hermes, b"Filt1 q", amount * 0.45)
        _ctl(self.fx, self._hermes, b"Filt1 resonance", amount * 0.35)
        _ctl(self.fx, self._hermes, b"Filt1 LFO1 level", amount * 280.0)
        _ctl(self.fx, self._hermes, b"Dist1 drive", amount * 1.4)

    def _beat_seconds(self, bpm):
        try:
            bpm = float(bpm)
        except (TypeError, ValueError):
            bpm = 120.0
        if bpm < 20.0:
            bpm = 120.0
        return max(0.05, min(4.0, 60.0 / bpm))

    def _apply_delay(self, delay_kind, bpm):
        if delay_kind not in ("delay", "tape") or not self._delay_kind:
            return
        beat = self._beat_seconds(bpm)
        dry = 0.0
        if delay_kind == "delay":
            speed = 1.0
            tap1, lvl1 = beat, -8.0
            tap2, lvl2 = min(4.0, beat * 2.0), -90.0
            tap3, lvl3 = min(4.0, beat * 3.0), -90.0
        else:
            speed = 0.82
            tap1, lvl1 = min(4.0, beat * speed), -6.0
            tap2, lvl2 = min(4.0, beat * 2.0 * speed), -14.0
            tap3, lvl3 = min(4.0, beat * 3.0 * speed), -22.0
        _ctl(self.fx, self._delays, b"Tape speed (inches/sec, 1=normal)", speed)
        _ctl(self.fx, self._delays, b"Dry level (dB)", dry)
        _ctl(self.fx, self._delays, b"Tap 1 distance (inches)", tap1)
        _ctl(self.fx, self._delays, b"Tap 1 level (dB)", lvl1)
        _ctl(self.fx, self._delays, b"Tap 2 distance (inches)", tap2)
        _ctl(self.fx, self._delays, b"Tap 2 level (dB)", lvl2)
        _ctl(self.fx, self._delays, b"Tap 3 distance (inches)", tap3)
        _ctl(self.fx, self._delays, b"Tap 3 level (dB)", lvl3)
        _ctl(self.fx, self._delays, b"Tap 4 distance (inches)", min(4.0, beat * 4.0))
        _ctl(self.fx, self._delays, b"Tap 4 level (dB)", -90.0)

    def apply(self, effect_id, amount, delay_kind=None, bpm=None):
        """Activa un efecto de Robot y/o el delay de sala.

        amount 0 o sin id = Robot limpio. El delay (delay/cinta) se queda
        montado si está elegido; el tempo viene del BPM de TCP.
        """
        if not self.loaded:
            return
        amount = max(0.0, min(1.0, float(amount)))
        if amount < 0.02:
            effect_id = None
        delay_kind = delay_kind if delay_kind in ("delay", "tape") else None
        if not effect_id and not delay_kind:
            self._tear_down()
            return
        key = (effect_id, bool(delay_kind))
        current = (self._active_id, bool(self._delay_kind))
        if current != key:
            if not self._build(effect_id, delay_kind):
                return
        self._delay_kind = delay_kind
        if effect_id == "phaser":
            self._apply_phaser(amount)
        elif effect_id == "bitcrush":
            self._apply_bitcrush(amount)
        elif effect_id == "flanger":
            self._apply_flanger(amount)
        elif effect_id == "wah":
            self._apply_wah(amount)
        elif effect_id == "ringmod":
            self._apply_ringmod(amount)
        elif effect_id == "distort":
            self._apply_distort(amount)
        elif effect_id == "djflanger":
            self._apply_djflanger(amount)
        elif effect_id == "valve":
            self._apply_valve(amount)
        elif effect_id == "crossover":
            self._apply_crossover(amount)
        elif effect_id == "wavewrap":
            self._apply_wavewrap(amount)
        elif effect_id == "sifter":
            self._apply_sifter(amount)
        elif effect_id == "comb":
            self._apply_comb(amount)
        elif effect_id == "smoothdecim":
            self._apply_smoothdecim(amount)
        elif effect_id == "rateshift":
            self._apply_rateshift(amount)
        elif effect_id == "hermes":
            self._apply_hermes(amount)
        self._apply_delay(delay_kind, bpm)

    def set_tempo(self, bpm):
        if self.loaded and self._delay_kind:
            self._apply_delay(self._delay_kind, bpm)

    def set_robot(self, t):
        """Compatibilidad: t bipolar, >0 phaser, <0 bitcrush (demo.py)."""
        t = max(-1.0, min(1.0, float(t)))
        if t >= 0:
            self.apply("phaser", t)
        else:
            self.apply("bitcrush", -t)
