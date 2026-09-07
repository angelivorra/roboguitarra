"""Catálogo de efectos robóticos disponibles.

Cada preset elige dos: uno para el knob/joystick hacia arriba y otro
hacia abajo. El centro sigue siendo limpio. El CC MIDI del hardware
usa el mismo mapeo bipolar que la web.
"""
import json

import config

EFFECTS = [
    {
        "id": "phaser",
        "name": "Phaser metálico",
        "description": "LFO phaser + bits extremos",
    },
    {
        "id": "bitcrush",
        "name": "Bitcrush 8-bit",
        "description": "Decimator de bits y sample rate",
    },
    {
        "id": "flanger",
        "name": "Flanger",
        "description": "Barrido de delay corto",
    },
    {
        "id": "wah",
        "name": "Wah",
        "description": "Filtro paso bajo (cierra agudos)",
    },
    {
        "id": "ringmod",
        "name": "Ring mod",
        "description": "Modulador en anillo (timbre metálico)",
    },
    {
        "id": "distort",
        "name": "Distorsión",
        "description": "Saturación Chebyshev + bits",
    },
    {
        "id": "djflanger",
        "name": "DJ flanger",
        "description": "Barrido agresivo tipo DJ",
    },
    {
        "id": "valve",
        "name": "Válvulas",
        "description": "Saturación de válvulas",
    },
    {
        "id": "crossover",
        "name": "Crossover",
        "description": "Distorsión de cruce (clase B)",
    },
    {
        "id": "wavewrap",
        "name": "Wavewrap",
        "description": "Plegado senoidal",
    },
    {
        "id": "sifter",
        "name": "Sifter",
        "description": "Reordena samples (glitch)",
    },
    {
        "id": "comb",
        "name": "Peine",
        "description": "Filtro peine metálico",
    },
    {
        "id": "smoothdecim",
        "name": "Decimate suave",
        "description": "Crush menos digital",
    },
    {
        "id": "rateshift",
        "name": "Rate shift",
        "description": "Estira o comprime el tiempo",
    },
    {
        "id": "hermes",
        "name": "Hermes",
        "description": "Filtro loco con LFO",
    },
]

DEFAULT_EFFECT_UP = "phaser"
DEFAULT_EFFECT_DOWN = "bitcrush"

DELAY_KINDS = [
    {"id": "none", "name": "Ninguno"},
    {"id": "delay", "name": "Delay"},
    {"id": "tape", "name": "Cinta"},
]
DEFAULT_DELAY = "none"


def list_effects():
    return list(EFFECTS)


def get_effect(effect_id):
    for item in EFFECTS:
        if item["id"] == effect_id:
            return item
    return EFFECTS[0]


def known_effect(effect_id):
    return any(item["id"] == effect_id for item in EFFECTS)


def list_delays():
    return list(DELAY_KINDS)


def known_delay(delay_id):
    return any(item["id"] == delay_id for item in DELAY_KINDS)


def _pick(overlay, saved, key):
    if isinstance(overlay, dict) and key in overlay:
        return overlay[key]
    if isinstance(saved, dict) and key in saved:
        return saved[key]
    return None


def resolve_pair(item, overlay=None, saved=None):
    """Devuelve (effect_up, effect_down) para un preset.

    Orden: overlay de sesión → predeterminado guardado → catálogo.
    """
    overlay = overlay if isinstance(overlay, dict) else {}
    saved = saved if isinstance(saved, dict) else {}
    catalog_up = (item or {}).get("effect_up")
    catalog_down = (item or {}).get("effect_down")
    up = overlay.get("up") or saved.get("up") or catalog_up or DEFAULT_EFFECT_UP
    down = overlay.get("down") or saved.get("down") or catalog_down or DEFAULT_EFFECT_DOWN
    if not known_effect(up):
        up = DEFAULT_EFFECT_UP
    if not known_effect(down):
        down = DEFAULT_EFFECT_DOWN
    return up, down


def clamp_send(value):
    try:
        return max(0, min(127, int(value)))
    except (TypeError, ValueError):
        return None


def clamp_gain(value):
    try:
        return max(0.0, min(2.0, float(value)))
    except (TypeError, ValueError):
        return None


def resolve_mix(overlay=None, saved=None):
    """Gain, envíos y on/off de sala: overlay de sesión → guardado.

    Solo incluye claves que existan en alguno de los dos; el resto se deja.
    """
    out = {}
    gain = clamp_gain(_pick(overlay, saved, "gain"))
    if gain is not None:
        out["gain"] = gain
    reverb = clamp_send(_pick(overlay, saved, "reverb_send"))
    if reverb is not None:
        out["reverb_send"] = reverb
    chorus = clamp_send(_pick(overlay, saved, "chorus_send"))
    if chorus is not None:
        out["chorus_send"] = chorus
    space = _pick(overlay, saved, "space_on")
    if space is not None:
        out["space_on"] = bool(space)
    delay = _pick(overlay, saved, "delay_kind")
    if delay is not None and known_delay(delay):
        out["delay_kind"] = delay
    return out


def load_saved_defaults():
    path = config.EFFECT_DEFAULTS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, value in data.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            out[idx] = value
    return out


def saved_default_for(index):
    return load_saved_defaults().get(int(index))


def save_preset_default(index, up, down, filename=None, mix=None):
    if not known_effect(up) or not known_effect(down):
        raise ValueError("Efecto desconocido")
    data = load_saved_defaults()
    entry = {"up": up, "down": down, "filename": filename}
    mix = resolve_mix(mix, None)
    entry.update(mix)
    data[int(index)] = entry
    serial = {str(k): v for k, v in sorted(data.items())}
    path = config.EFFECT_DEFAULTS_FILE
    path.write_text(json.dumps(serial, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data[int(index)]
